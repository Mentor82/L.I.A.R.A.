from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest

from fastapi.testclient import TestClient

from services.contracts import (
    StateEvidence,
    ValidatorFinding,
    ValidatorResultResponse,
    ValidatorStatusResponse,
    ValidatorSubmitResponse,
)
from services.contracts.service_boundaries import MemoryServiceStatus
from services.self_observer.app import create_self_observer_app
from services.self_observer.assurance import SelfInspectionGate
from services.self_observer.core import SelfObserverInstance
from services.self_observer.probes import SelfObserverProbes
from services.contracts.heartbeat import StateCurve


def _evidence(*, assurance_state: str = "healthy", capacity: float = 0.8) -> list[StateEvidence]:
    now = datetime.now(UTC)
    return [
        StateEvidence(
            domain="hardware", source_id="heartbeat", observed_at=now,
            state="healthy", confidence=0.95, stability=0.9,
            attributes={"capacity": capacity, "active_work": 0, "active_work_observed_at": now.isoformat()},
        ),
        StateEvidence(
            domain="software", source_id="health", observed_at=now,
            state="healthy", confidence=1.0, stability=1.0,
            attributes={"backends_healthy": 6, "backends_total": 6},
        ),
        StateEvidence(
            domain="assurance", source_id="ai-validator", observed_at=now,
            state=assurance_state, confidence=0.95, stability=1.0,
            signals=["validator_findings"] if assurance_state == "attention" else [],
            attributes={"findings_count": 1 if assurance_state == "attention" else 0},
        ),
    ]


def test_backend_probe_timeout_is_separate_and_never_lower_than_fast_timeout():
    probes = SelfObserverProbes(
        memory_base_url="http://127.0.0.1:8020/",
        timeout_seconds=4.0,
        backend_timeout_seconds=12.0,
    )
    clamped = SelfObserverProbes(timeout_seconds=6.0, backend_timeout_seconds=2.0)

    assert probes.timeout_seconds == 4.0
    assert probes.backend_timeout_seconds == 12.0
    assert probes.memory_base_url == "http://127.0.0.1:8020"
    assert clamped.backend_timeout_seconds == 6.0


def test_observer_requires_stable_cycles_before_quiet_candidate(tmp_path):
    instance = SelfObserverInstance(
        store_dir=tmp_path,
        quiet_candidate_cycles=2,
        quiet_stable_cycles=4,
    )

    phases = [instance.observe(_evidence()).phase for _ in range(4)]

    assert phases == ["observing", "quiet_candidate", "quiet_candidate", "quiet_stable"]
    assert instance.latest().background_analysis_candidate is True
    assert instance.latest().state == "healthy"


def test_validator_finding_prevents_quiet_state_and_remains_visible(tmp_path):
    instance = SelfObserverInstance(store_dir=tmp_path)
    instance.observe(_evidence())

    result = instance.observe(_evidence(assurance_state="attention"))

    assert result.state == "attention"
    assert result.phase == "observing"
    assert result.quiet_cycles == 0
    assert result.background_analysis_candidate is False
    assert "validator_findings" in result.signals


def test_observer_persists_latest_and_resumes_sequence(tmp_path):
    first = SelfObserverInstance(store_dir=tmp_path)
    state = first.observe(_evidence())

    resumed = SelfObserverInstance(store_dir=tmp_path)

    assert resumed.latest() == state
    assert resumed.observe(_evidence()).sequence == state.sequence + 1
    assert (tmp_path / "history.jsonl").read_text(encoding="utf-8").count("\n") == 2


class _FakeProbes:
    async def collect(self):
        return _evidence()


class _FakeSubmitter:
    def __init__(self):
        self.requests = []

    async def submit(self, request):
        self.requests.append(request)
        return ValidatorSubmitResponse(
            job_id="validator-job-1",
            state="queued",
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def status(self, request):
        return ValidatorStatusResponse(
            job_id=request.job_id,
            state="completed",
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def result(self, request):
        return ValidatorResultResponse(
            job_id=request.job_id,
            state="completed",
            findings=[ValidatorFinding(severity="warning", message="contract drift")],
            artifacts=["report.json"],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
            summary={"exit_code": 0, "scope": "quick"},
        )


class _TransitionSubmitter(_FakeSubmitter):
    def __init__(self):
        super().__init__()
        self.status_calls = 0

    async def status(self, request):
        self.status_calls += 1
        return ValidatorStatusResponse(
            job_id=request.job_id,
            state="running" if self.status_calls == 1 else "completed",
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )


class _FailedSubmitter(_FakeSubmitter):
    async def result(self, request):
        return ValidatorResultResponse(
            job_id=request.job_id,
            state="failed",
            findings=[ValidatorFinding(severity="error", message="validator exited with code 137")],
            artifacts=["run.log"],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
            summary={"exit_code": 137, "scope": "quick"},
        )


async def test_inspection_gate_observe_mode_exposes_eligibility_without_submission(tmp_path):
    instance = SelfObserverInstance(store_dir=tmp_path, quiet_candidate_cycles=1, quiet_stable_cycles=2)
    instance.observe(_evidence())
    state = instance.observe(_evidence())
    submitter = _FakeSubmitter()
    gate = SelfInspectionGate(mode="observe", store_dir=tmp_path, submitter=submitter)

    decision = await gate.evaluate(state)

    assert decision.eligible is True
    assert decision.action == "would_submit"
    assert submitter.requests == []


async def test_inspection_gate_submit_mode_is_traceable_and_rate_limited(tmp_path):
    instance = SelfObserverInstance(store_dir=tmp_path, quiet_candidate_cycles=1, quiet_stable_cycles=2)
    instance.observe(_evidence())
    state = instance.observe(_evidence())
    submitter = _FakeSubmitter()
    gate = SelfInspectionGate(
        mode="submit",
        workspace="C:/ai/LIARA",
        minimum_interval_seconds=3600,
        store_dir=tmp_path,
        submitter=submitter,
    )

    submitted = await gate.evaluate(state)
    limited = await gate.evaluate(state)

    assert submitted.action == "submitted"
    assert submitted.job_id == "validator-job-1"
    assert submitter.requests[0].source == "liara-self-inspection-gate"
    assert submitter.requests[0].metadata["observer_sequence"] == state.sequence
    assert limited.eligible is False
    assert "minimum_interval_not_elapsed" in limited.reasons
    assert len(submitter.requests) == 1
    assert SelfInspectionGate(mode="observe", store_dir=tmp_path).latest().job_id == "validator-job-1"


async def test_inspection_gate_closes_validator_feedback_loop_with_structured_findings(tmp_path):
    instance = SelfObserverInstance(store_dir=tmp_path / "observer", quiet_candidate_cycles=1, quiet_stable_cycles=2)
    instance.observe(_evidence())
    state = instance.observe(_evidence())
    gate = SelfInspectionGate(
        mode="submit",
        workspace="C:/ai/LIARA",
        store_dir=tmp_path / "gate",
        submitter=_FakeSubmitter(),
    )
    await gate.evaluate(state)

    completed = await gate.refresh()
    evidence = gate.assurance_evidence()

    assert completed.job_state == "completed"
    assert completed.action == "completed"
    assert completed.findings[0].message == "contract drift"
    assert completed.artifacts == ["report.json"]
    assert completed.exit_code == 0
    assert evidence.state == "attention"
    assert evidence.attributes["findings_count"] == 1
    assert evidence.attributes["highest_severity"] == "warning"
    assert "validator_findings" in evidence.signals

    observed = instance.observe([
        item for item in _evidence() if item.domain != "assurance"
    ] + [evidence])
    assert observed.state == "attention"
    assert observed.phase == "observing"
    assert "validator_findings" in observed.signals


@pytest.mark.parametrize("tail,exit_code,expected,domain", [
    ("error", 1, "failed", "degraded"),
    ("warning", 0, "attention", "attention"),
    (None, 0, "passed", "healthy"),
    (None, 1, "failed", "degraded"),
    (None, None, "unknown", "attention"),
])
async def test_full_quality_survives_preview_persistence_and_evaluate(tmp_path, tail, exit_code, expected, domain):
    findings = [ValidatorFinding(severity="info", message=f"known-{i}", metadata={"disposition": "baseline"}) for i in range(100)]
    if tail:
        findings.append(ValidatorFinding(severity=tail, message="beyond-preview"))

    class Submitter(_FakeSubmitter):
        async def result(self, request):
            return ValidatorResultResponse(job_id=request.job_id, state="completed", findings=findings,
                                           summary={"exit_code": exit_code},
                                           status=MemoryServiceStatus(status="success", backend="memory-service"))

    observer = SelfObserverInstance(store_dir=tmp_path / "observer", quiet_candidate_cycles=1, quiet_stable_cycles=2)
    observer.observe(_evidence())
    state = observer.observe(_evidence())
    gate = SelfInspectionGate(mode="submit", workspace="C:/ai/LIARA", store_dir=tmp_path / "gate", submitter=Submitter())
    await gate.evaluate(state)
    result = await gate.refresh()
    assert result.job_state == "completed"
    assert len(result.findings) == 100
    assert result.findings_total == len(findings)
    assert result.quality_verdict == expected
    assert result.disposition_counts["baseline"] == 100
    assert result.disposition_counts["new"] == int(tail is not None)
    app = create_self_observer_app(observer, probes=_FakeProbes(), inspection_gate=gate, enable_collector=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://observer") as client:
        response = await client.get("/v1/inspection")
    assert response.status_code == 200
    assert response.json()["quality_verdict"] == expected
    assert response.json()["findings_total"] == len(findings)
    assert len(response.json()["findings"]) == 100
    for candidate in (gate, SelfInspectionGate(mode="observe", workspace="C:/ai/LIARA", store_dir=tmp_path / "gate")):
        evidence = candidate.assurance_evidence()
        assert evidence.state == domain
        assert evidence.attributes["quality_verdict"] == expected
        assert evidence.attributes["findings_count"] == len(findings)
        decision = await candidate.evaluate(state)
        assert decision.quality_verdict == expected
        assert decision.findings_total == len(findings)
        observed = observer.observe([e for e in _evidence() if e.domain != "assurance"] + [evidence])
        if expected != "passed":
            assert observed.phase != "quiet_stable"
            assert observed.background_analysis_candidate is False
            assert (await candidate.evaluate(observed)).eligible is False
        if expected in {"failed", "attention", "unknown"}:
            # Technical completion cannot disable the explicit operator-only
            # recovery path; cyclic submission remains blocked by assurance.
            assert candidate._is_failed_assurance_retry(observed)
            retry = await candidate.submit_canary(observed, authorization_id="operator-test-authorization",
                                                 reason="operator requested quality refresh")
            if candidate.submitter is not None:
                assert retry.action == "submitted"
                assert retry.quality_verdict is None


async def test_legacy_or_inconsistent_quality_cannot_be_healthy(tmp_path):
    observer = SelfObserverInstance(store_dir=tmp_path / "observer", quiet_candidate_cycles=1, quiet_stable_cycles=2)
    observer.observe(_evidence())
    state = observer.observe(_evidence())
    gate = SelfInspectionGate(mode="submit", workspace="C:/ai/LIARA", store_dir=tmp_path / "gate", submitter=_FakeSubmitter())
    await gate.evaluate(state)
    result = await gate.refresh()
    for update in [
        {"quality_verdict": None, "findings_total": None, "severity_counts": {}, "highest_severity": None},
        {"quality_verdict": "passed"},
        {"findings_total": 0},
    ]:
        gate._last_decision = result.model_copy(update=update)
        gate._persist(gate._last_decision)
        loaded = SelfInspectionGate(mode="observe", workspace="C:/ai/LIARA", store_dir=tmp_path / "gate")
        assert loaded.assurance_evidence().state != "healthy"


async def test_operator_canary_uses_real_observed_state_and_records_complete_transitions(tmp_path):
    state = SelfObserverInstance(store_dir=tmp_path / "observer").observe(_evidence(capacity=0.1))
    assert state.state == "healthy"
    assert state.phase == "observing"
    submitter = _TransitionSubmitter()
    gate = SelfInspectionGate(
        mode="observe",
        workspace="C:/ai/LIARA",
        store_dir=tmp_path / "gate",
        submitter=submitter,
    )

    submitted = await gate.submit_canary(
        state,
        authorization_id="operator-canary-001",
        reason="calibrate real assurance feedback",
    )
    running = await gate.refresh()
    completed = await gate.refresh()

    assert submitted.trigger == "operator_canary"
    assert submitted.authorization_id == "operator-canary-001"
    assert submitted.scope == "quick"
    assert submitted.request_id == submitted.run_id
    assert submitted.request_id.startswith("self-inspection-")
    assert submitted.observer_sequence == state.sequence
    assert submitter.requests[0].scope == "quick"
    assert submitter.requests[0].metadata["observer_phase"] == "observing"
    assert submitter.requests[0].metadata["inspection_trigger"] == "operator_canary"
    assert running.job_state == "running"
    assert [item.state for item in completed.transitions] == ["queued", "running", "completed"]
    assert completed.exit_code == 0


@pytest.mark.parametrize("workload", ["known", "missing", "stale", "invalid"])
@pytest.mark.parametrize("hardware_fault", [False, True])
async def test_operator_canary_can_refresh_stale_terminal_assurance_only(tmp_path, workload, hardware_fault):
    metrics = {"known": [_work_curve()], "missing": [], "stale": [_work_curve(age=60)],
               "invalid": [_work_curve(-1)]}[workload]
    hardware = await _probe_workload(metrics)
    if workload != "known":
        assert hardware.signals
    if hardware_fault:
        hardware = hardware.model_copy(update={"signals": [*hardware.signals, "hardware_source_unreachable"]})
    submitter = _FakeSubmitter()
    gate = SelfInspectionGate(
        mode="observe",
        workspace="C:/ai/LIARA",
        minimum_interval_seconds=60,
        evidence_stale_seconds=60,
        store_dir=tmp_path / "gate",
        submitter=submitter,
    )
    healthy = SelfObserverInstance(store_dir=tmp_path / "healthy").observe(_evidence())
    first = await gate.submit_canary(
        healthy,
        authorization_id="operator-canary-initial",
        reason="establish terminal validator evidence",
    )
    await gate.refresh()

    stale_at = datetime.now(UTC) - timedelta(seconds=120)
    stale_evidence = [
        hardware if item.domain == "hardware" else item
        for item in _evidence() if item.domain != "assurance"
    ] + [StateEvidence(
        domain="assurance",
        source_id="self-inspection-gate",
        observed_at=stale_at,
        state="degraded",
        confidence=0.6,
        stability=0.25,
        signals=["validator_job_failed", "validator_findings", "validator_evidence_stale"],
        attributes={"job_state": "failed"},
    )]
    degraded = SelfObserverInstance(store_dir=tmp_path / "degraded").observe(stale_evidence)

    recovered = await gate.submit_canary(
        degraded,
        authorization_id="operator-canary-recovery",
        reason="refresh stale terminal assurance evidence",
        now=first.last_submitted_at + timedelta(seconds=61),
    )

    assert degraded.state == "degraded"
    if hardware_fault:
        assert recovered.action == "none"
        assert "system_not_healthy" in recovered.reasons
        assert len(submitter.requests) == 1
        return

    assert recovered.action == "submitted"
    assert "operator_recovery_from_stale_assurance" in recovered.reasons
    assert len(submitter.requests) == 2


async def test_stale_assurance_recovery_does_not_bypass_software_degradation(tmp_path):
    submitter = _FakeSubmitter()
    gate = SelfInspectionGate(
        mode="observe",
        workspace="C:/ai/LIARA",
        minimum_interval_seconds=60,
        evidence_stale_seconds=60,
        store_dir=tmp_path / "gate",
        submitter=submitter,
    )
    healthy = SelfObserverInstance(store_dir=tmp_path / "healthy").observe(_evidence())
    first = await gate.submit_canary(
        healthy,
        authorization_id="operator-canary-initial",
        reason="establish terminal validator evidence",
    )
    await gate.refresh()
    stale_at = datetime.now(UTC) - timedelta(seconds=120)
    degraded_evidence = [
        item.model_copy(update={
            "state": "degraded",
            "signals": ["software_source_unreachable"],
        }) if item.domain == "software" else item
        for item in _evidence()
        if item.domain != "assurance"
    ] + [StateEvidence(
        domain="assurance",
        source_id="self-inspection-gate",
        observed_at=stale_at,
        state="degraded",
        confidence=0.6,
        stability=0.25,
        signals=["validator_evidence_stale"],
    )]
    degraded = SelfObserverInstance(store_dir=tmp_path / "degraded").observe(degraded_evidence)

    blocked = await gate.submit_canary(
        degraded,
        authorization_id="operator-canary-blocked",
        reason="must not bypass software degradation",
        now=first.last_submitted_at + timedelta(seconds=61),
    )

    assert blocked.action == "none"
    assert "system_not_healthy" in blocked.reasons
    assert len(submitter.requests) == 1


@pytest.mark.parametrize("workload", ["known", "missing", "stale", "invalid"])
@pytest.mark.parametrize("hardware_fault", [False, True])
async def test_operator_can_retry_failed_assurance_before_regular_minimum_interval(tmp_path, workload, hardware_fault):
    metrics = {"known": [_work_curve()], "missing": [], "stale": [_work_curve(age=60)],
               "invalid": [_work_curve(-1)]}[workload]
    hardware = await _probe_workload(metrics)
    if workload != "known":
        assert hardware.signals
    if hardware_fault:
        hardware = hardware.model_copy(update={"signals": [*hardware.signals, "hardware_source_unreachable"]})
    submitter = _FailedSubmitter()
    gate = SelfInspectionGate(
        mode="observe",
        workspace="C:/ai/LIARA",
        minimum_interval_seconds=21_600,
        store_dir=tmp_path / "gate",
        submitter=submitter,
    )
    healthy = SelfObserverInstance(store_dir=tmp_path / "healthy").observe(_evidence())
    first = await gate.submit_canary(
        healthy,
        authorization_id="operator-canary-initial",
        reason="establish failed validator evidence",
    )
    await gate.refresh()
    failed_evidence = gate.assurance_evidence()
    assert failed_evidence is not None
    degraded = SelfObserverInstance(store_dir=tmp_path / "degraded").observe([
        hardware if item.domain == "hardware" else item
        for item in _evidence() if item.domain != "assurance"
    ] + [failed_evidence])

    retried = await gate.submit_canary(
        degraded,
        authorization_id="operator-canary-retry",
        reason="retry after bounded validator repair",
        now=first.last_submitted_at + timedelta(seconds=1),
    )

    if hardware_fault:
        assert retried.action == "none"
        assert "system_not_healthy" in retried.reasons
        assert len(submitter.requests) == 1
        return

    assert retried.action == "submitted"
    assert "operator_retry_after_failed_assurance" in retried.reasons
    assert "minimum_interval_not_elapsed" not in retried.reasons
    assert len(submitter.requests) == 2


def test_canary_endpoint_is_single_use_per_process(monkeypatch, tmp_path):
    monkeypatch.setenv("LIARA_SELF_INSPECTION_CANARY_ENABLED", "true")
    monkeypatch.setenv("LIARA_SELF_INSPECTION_CANARY_TOKEN", "single-use-token")
    instance = SelfObserverInstance(store_dir=tmp_path / "observer")
    gate = SelfInspectionGate(
        mode="observe",
        workspace="C:/ai/LIARA",
        store_dir=tmp_path / "gate",
        submitter=_FakeSubmitter(),
    )
    app = create_self_observer_app(
        instance,
        probes=_FakeProbes(),
        inspection_gate=gate,
        enable_collector=False,
    )
    headers = {"Authorization": "Bearer single-use-token"}

    with TestClient(app) as client:
        first = client.post(
            "/v1/inspection/canary",
            headers=headers,
            json={"authorization_id": "operator-canary-first", "reason": "single authorized canary"},
        )
        replay = client.post(
            "/v1/inspection/canary",
            headers=headers,
            json={"authorization_id": "operator-canary-second", "reason": "must be rejected as replay"},
        )

    assert first.status_code == 200
    assert first.json()["action"] == "submitted"
    assert replay.status_code == 403


async def test_inspection_gate_rejects_non_quiet_or_unconfigured_submission(tmp_path):
    state = SelfObserverInstance(store_dir=tmp_path).observe(_evidence(capacity=0.1))
    gate = SelfInspectionGate(mode="submit", workspace=None, store_dir=tmp_path)

    decision = await gate.evaluate(state)

    assert decision.eligible is False
    assert set(decision.reasons) == {"quiet_state_not_stable", "validator_workspace_not_configured"}


def test_api_is_read_only_and_exposes_state_history_and_human_status(tmp_path):
    instance = SelfObserverInstance(store_dir=tmp_path)
    app = create_self_observer_app(instance, probes=_FakeProbes(), enable_collector=False)

    with TestClient(app) as client:
        state = client.get("/v1/state")
        history = client.get("/v1/history", params={"limit": 1})
        inspection = client.get("/v1/inspection")
        unauthorized_canary = client.post(
            "/v1/inspection/canary",
            json={"authorization_id": "operator-canary-001", "reason": "test authorization boundary"},
        )
        human = client.get("/v1/status.txt")

    assert state.status_code == 200
    assert state.headers["cache-control"] == "no-store"
    assert state.json()["observer_id"] == "liara.instance.self-observer.local"
    assert len(history.json()) == 1
    assert inspection.status_code == 200
    assert inspection.json()["mode"] == "observe"
    assert unauthorized_canary.status_code == 403
    assert "sources=assurance:healthy" in human.text
    assert "inspection=observe:none" in human.text


def _work_curve(value=0, *, age=0, device="default"):
    timestamp = datetime.now(UTC) - timedelta(seconds=age)
    return {
        "key": f"system:{device}:active_work", "resource": "system", "metric": "active_work",
        "unit": "count", "device_id": device, "current": value, "minimum": value,
        "maximum": value, "mean": value, "slope_per_minute": 0, "sample_count": 4,
        "duration_seconds": 90, "confidence": 1, "last_observed_at": timestamp.isoformat(),
    }


async def _probe_workload(metrics, *, validate=False):
    payload = {
        "instance_id": "test-heartbeat", "node_id": "test-node", "generated_at": datetime.now(UTC).isoformat(),
        "window_seconds": 300, "state": "healthy", "trend": "stable", "confidence": .95,
        "stability": 1, "metrics": metrics, "envelope": {"capacity": .8, "max_parallel_jobs": 2},
    }
    # The normal fixture is the actual /v1/curve contract, not an invented
    # envelope.active_work field. Invalid metric cases are intentionally raw.
    if validate:
        StateCurve.model_validate(payload)
    original_client = httpx.AsyncClient

    def handle(request):
        assert request.url.path == "/v1/curve"
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handle)
    with patch("services.self_observer.probes.httpx.AsyncClient",
               side_effect=lambda **kwargs: original_client(transport=transport, **kwargs)):
        return await SelfObserverProbes().hardware()


@pytest.mark.parametrize("value", [0, 0.4, 3])
async def test_canonical_workload_controls_quiet_phase(tmp_path, value):
    hardware = await _probe_workload([_work_curve(value)], validate=True)
    assert hardware.attributes["active_work"] == value
    assert hardware.attributes["active_work_state"] == "known"
    assert hardware.attributes["active_work_observed_at"]
    observer = SelfObserverInstance(store_dir=tmp_path)
    for _ in range(4):
        items = _evidence()
        items[0] = hardware
        state = observer.observe(items)
    assert state.background_analysis_candidate is (value == 0)


@pytest.mark.parametrize("case", ["missing", "stale", "future", "low_confidence", "negative", "nan", "unit", "malformed", "naive"])
async def test_unknown_or_invalid_workload_never_proves_idle(tmp_path, case):
    metric = _work_curve()
    signal = "active_work_invalid"
    if case == "stale":
        metric = _work_curve(age=120)
        signal = "active_work_stale"
    elif case == "future":
        metric = _work_curve(age=-120)
        signal = "active_work_stale"
    elif case == "low_confidence":
        metric["confidence"] = .5
    elif case == "negative":
        metric["current"] = -1
    elif case == "nan":
        metric["current"] = "NaN"
    elif case == "unit":
        metric["unit"] = "ratio"
    elif case == "malformed":
        del metric["last_observed_at"]
    elif case == "naive":
        metric["last_observed_at"] = datetime.now().isoformat()
    if case == "missing":
        signal = "active_work_unknown"
    hardware = await _probe_workload([] if case == "missing" else [metric])
    assert hardware.state == "healthy"  # Unknown work is not broken hardware.
    assert signal in hardware.signals
    assert hardware.attributes["active_work"] is None
    assert hardware.attributes["active_work_state"] == ("stale" if signal == "active_work_stale" else "unknown")
    observer = SelfObserverInstance(store_dir=tmp_path)
    for _ in range(4):
        items = _evidence()
        items[0] = hardware
        state = observer.observe(items)
    assert state.phase == "observing"
    assert not state.background_analysis_candidate


async def test_all_advertised_workload_metrics_must_be_fresh():
    hardware = await _probe_workload([_work_curve(device="cpu"), _work_curve(age=120, device="gpu")])
    assert hardware.attributes["active_work"] is None
    hardware = await _probe_workload([_work_curve(0, device="cpu"), _work_curve(2, device="gpu")])
    assert hardware.attributes["active_work"] == 2


def test_workload_freshness_defaults_and_app_configuration(monkeypatch, tmp_path):
    assert SelfObserverProbes().workload_stale_seconds == 15
    assert SelfObserverInstance(store_dir=tmp_path).workload_stale_seconds == 15
    assert SelfObserverProbes(workload_stale_seconds=0).workload_stale_seconds == 1
    monkeypatch.setenv("LIARA_SELF_OBSERVER_WORKLOAD_STALE_SECONDS", "23")
    monkeypatch.setenv("LIARA_SELF_OBSERVER_STORE_DIR", str(tmp_path))
    with patch("services.self_observer.app.SelfObserverProbes", wraps=SelfObserverProbes) as probe_class:
        app = create_self_observer_app(enable_collector=False)
    assert probe_class.call_args.kwargs["workload_stale_seconds"] == 23
    assert app.state.self_observer.workload_stale_seconds == 23


@pytest.mark.parametrize("value", [None, -1, float("nan"), True])
def test_direct_evidence_cannot_default_missing_or_invalid_work_to_idle(tmp_path, value):
    items = _evidence()
    if value is None:
        items[0].attributes.pop("active_work")
    else:
        items[0].attributes["active_work"] = value
    observer = SelfObserverInstance(store_dir=tmp_path)
    for _ in range(4):
        state = observer.observe(items)
    assert state.quiet_cycles == 0


async def test_reused_idle_measurement_expires_and_resets_hysteresis(tmp_path):
    hardware = await _probe_workload([_work_curve()])
    items = _evidence()
    items[0] = hardware
    observer = SelfObserverInstance(store_dir=tmp_path, workload_stale_seconds=60)
    for _ in range(4):
        assert observer.observe(items).state == "healthy"
    assert observer.latest().phase == "quiet_stable"
    expired = observer.observe(items, now=datetime.now(UTC) + timedelta(seconds=61))
    assert expired.phase == "observing" and expired.quiet_cycles == 0


@pytest.mark.parametrize("timestamp", [None, "bad", datetime.now().isoformat()])
def test_missing_or_invalid_work_timestamp_is_not_idle(tmp_path, timestamp):
    items = _evidence()
    items[0].attributes["active_work_observed_at"] = timestamp
    observer = SelfObserverInstance(store_dir=tmp_path)
    for _ in range(4):
        result = observer.observe(items)
    assert result.quiet_cycles == 0


async def test_collector_preserves_active_job_feedback_and_tracking(tmp_path):
    observer = SelfObserverInstance(store_dir=tmp_path / "observer")
    for _ in range(4):
        state = observer.observe(_evidence())
    submitter = _FakeSubmitter()
    gate = SelfInspectionGate(mode="submit", workspace="test-only", minimum_interval_seconds=60,
                              store_dir=tmp_path / "gate", submitter=submitter)
    first = await gate.evaluate(state, now=datetime.now(UTC) - timedelta(seconds=61))
    async def running(request):
        return ValidatorStatusResponse(job_id=request.job_id, state="running",
            status=MemoryServiceStatus(status="success", backend="memory-service"))
    app = create_self_observer_app(observer, probes=_FakeProbes(), inspection_gate=gate, enable_collector=False)
    with patch.object(submitter, "status", side_effect=running):
        envelope = await app.state.collect_once()
    assert envelope.state == "attention" and envelope.quiet_cycles == 0
    assert "validator_job_in_progress" in envelope.signals
    assert not gate.latest().eligible
    assert "validator_job_already_active" in gate.latest().reasons
    assert gate.latest().job_id == first.job_id
    assert len(submitter.requests) == 1


@pytest.mark.parametrize("mode", ["observe", "submit"])
@pytest.mark.parametrize("job_state", ["queued", "running"])
async def test_active_job_blocks_even_after_interval_with_healthy_snapshot(tmp_path, mode, job_state):
    observer = SelfObserverInstance(store_dir=tmp_path / "observer")
    for _ in range(4):
        state = observer.observe(_evidence())
    submitter = _FakeSubmitter()
    gate = SelfInspectionGate(mode="submit", workspace="test-only", minimum_interval_seconds=60,
                              store_dir=tmp_path / "gate", submitter=submitter)
    old = datetime.now(UTC) - timedelta(seconds=61)
    first = await gate.evaluate(state, now=old)
    # Persist/reload the active decision: the guard must survive a restart.
    gate._last_decision = first.model_copy(update={"job_state": job_state})
    gate._persist(gate._last_decision)
    resumed = SelfInspectionGate(mode=mode, workspace="test-only", minimum_interval_seconds=60,
                                 store_dir=tmp_path / "gate", submitter=submitter)
    result = await resumed.evaluate(state)
    assert not result.eligible and result.action == "none"
    assert result.reasons == ["validator_job_already_active"]
    assert result.job_id == first.job_id and result.request_id == first.request_id
    assert result.last_submitted_at == first.last_submitted_at and result.job_state == job_state
    assert len(submitter.requests) == 1
