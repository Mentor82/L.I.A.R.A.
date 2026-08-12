from __future__ import annotations

from datetime import UTC, datetime, timedelta

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


def _evidence(*, assurance_state: str = "healthy", capacity: float = 0.8) -> list[StateEvidence]:
    now = datetime.now(UTC)
    return [
        StateEvidence(
            domain="hardware", source_id="heartbeat", observed_at=now,
            state="healthy", confidence=0.95, stability=0.9,
            attributes={"capacity": capacity, "active_work": 0},
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


async def test_operator_canary_can_refresh_stale_terminal_assurance_only(tmp_path):
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
        item for item in _evidence() if item.domain != "assurance"
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


async def test_operator_can_retry_failed_assurance_before_regular_minimum_interval(tmp_path):
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
        item for item in _evidence() if item.domain != "assurance"
    ] + [failed_evidence])

    retried = await gate.submit_canary(
        degraded,
        authorization_id="operator-canary-retry",
        reason="retry after bounded validator repair",
        now=first.last_submitted_at + timedelta(seconds=1),
    )

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
