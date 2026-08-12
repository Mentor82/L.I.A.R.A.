from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
import pytest

from services.config import Settings
from services.contracts import MemoryDreamingProposalRecord, MemoryLifecycleStatus
from services.memory import BackedMemoryServiceStore, InMemoryMemoryServiceStore, create_memory_service_app
import services.memory.store as memory_store_module


def test_validator_docker_cli_prefers_explicit_configuration(monkeypatch) -> None:
    configured = r"C:\tools\docker.exe"
    monkeypatch.setenv("LIARA_VALIDATOR_DOCKER_CLI", configured)
    monkeypatch.setattr(memory_store_module.os.path, "isfile", lambda path: path == configured)
    monkeypatch.setattr(memory_store_module.shutil, "which", lambda _: None)

    assert memory_store_module._resolve_validator_docker_cli() == configured


def test_validator_docker_cli_uses_platform_default_when_path_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("LIARA_VALIDATOR_DOCKER_CLI", raising=False)
    monkeypatch.setattr(memory_store_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(memory_store_module.os, "name", "nt")
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
    expected = r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"
    monkeypatch.setattr(memory_store_module.os.path, "isfile", lambda path: path == expected)

    assert memory_store_module._resolve_validator_docker_cli() == expected


def test_validator_wsl_staging_rejects_unapproved_distro(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(memory_store_module.os, "name", "nt")
    monkeypatch.setenv("LIARA_VALIDATOR_WSL_DISTROS", "Debian")

    with pytest.raises(ValueError, match="not approved"):
        memory_store_module._stage_validator_workspace_if_needed(
            r"\\wsl.localhost\Ubuntu\home\liara\workspace",
            str(tmp_path),
        )


def test_validator_wsl_staging_rejects_path_outside_workspace(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(memory_store_module.os, "name", "nt")
    monkeypatch.setenv("LIARA_VALIDATOR_WSL_DISTROS", "Debian")

    with pytest.raises(ValueError, match="outside"):
        memory_store_module._stage_validator_workspace_if_needed(
            r"\\wsl.localhost\Debian\etc",
            str(tmp_path),
        )


def test_validator_execution_backend_is_configurable_and_runtime_neutral(monkeypatch, tmp_path) -> None:
    observed: dict[str, Any] = {}

    class _VmBackend:
        name = "unit_vm"

        def execute(self, request):
            observed["request"] = request
            return {
                "state": "completed",
                "summary": {"exit_code": 0, "findings_count": 0},
                "findings": [],
                "artifacts": [],
            }

    memory_store_module.register_validator_execution_backend(_VmBackend(), replace=True)
    monkeypatch.setenv("LIARA_VALIDATOR_EXECUTION_MODE", "worker")
    monkeypatch.setenv("LIARA_VALIDATOR_BACKEND", "unit_vm")
    monkeypatch.setattr(
        memory_store_module,
        "persist_validation_report",
        lambda **_: tmp_path / "report.json",
    )

    result = memory_store_module._execute_validator_job(
        job_id="job-vm",
        workspace=str(tmp_path),
        scope="quick",
        checks=[],
        strict_mode=False,
        session_id="session-vm",
    )

    request = observed["request"]
    assert request.workspace == str(tmp_path)
    assert request.prepared_workspace == str(tmp_path)
    assert request.session_id == "session-vm"
    assert result["state"] == "completed"
    assert result["summary"]["execution_backend"] == "unit_vm"
    assert result["summary"]["execution_mode"] == "unit_vm"
    assert result["summary"]["workspace_preparation"]["staged"] is False
    assert result["summary"]["workspace_staging"] == result["summary"]["workspace_preparation"]


def test_validator_execution_backend_fails_closed_when_unregistered(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("LIARA_VALIDATOR_EXECUTION_MODE", "worker")
    monkeypatch.setenv("LIARA_VALIDATOR_BACKEND", "missing_vm")

    result = memory_store_module._execute_validator_job(
        job_id="job-missing-backend",
        workspace=str(tmp_path),
        scope="quick",
        checks=[],
        strict_mode=False,
    )

    assert result["state"] == "failed"
    assert result["summary"]["execution_backend"] == "missing_vm"
    assert result["summary"]["error"] == "validator_execution_backend_unavailable"
    assert "not registered" in result["findings"][0]["message"]


class _StubAsyncKVStore:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    async def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    async def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        del ttl_seconds
        self.data[key] = value

    async def delete(self, key: str) -> None:
        self.data.pop(key, None)

    async def exists(self, key: str) -> bool:
        return key in self.data


def test_staging_stage_list_discard_roundtrip() -> None:
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        stage_resp = client.post(
            "/staging/stage",
            json={
                "session_id": "sess-1",
                "run_id": "run-1",
                "content": "remember this",
                "source": "unit-test",
                "importance": 0.8,
                "access_count": 2,
                "ttl_seconds": 1800,
                "source_ids": ["turn:user:1", "turn:assistant:2"],
                "metadata": {"topic": "memory"},
            },
        )
        assert stage_resp.status_code == 200
        stage_payload = stage_resp.json()
        assert stage_payload["status"]["status"] == "success"
        assert len(stage_payload["items"]) == 1
        staged_id = stage_payload["items"][0]["staging_id"]
        assert stage_payload["items"][0]["importance"] == 0.8
        assert stage_payload["items"][0]["access_count"] == 2
        assert stage_payload["items"][0]["ttl_seconds"] == 1800
        assert stage_payload["items"][0]["source_ids"] == ["turn:user:1", "turn:assistant:2"]

        touch_resp = client.post(
            "/staging/touch",
            json={
                "session_id": "sess-1",
                "staging_ids": [staged_id],
                "access_increment": 2,
                "touch_reason": "used in unit test",
            },
        )
        assert touch_resp.status_code == 200
        touch_payload = touch_resp.json()
        assert len(touch_payload["items"]) == 1
        assert touch_payload["items"][0]["access_count"] == 4
        assert touch_payload["items"][0]["metadata"]["last_touch_reason"] == "used in unit test"

        list_resp = client.post(
            "/staging/list",
            json={"session_id": "sess-1", "status": "staged", "limit": 10},
        )
        assert list_resp.status_code == 200
        list_payload = list_resp.json()
        assert len(list_payload["items"]) == 1
        assert list_payload["items"][0]["staging_id"] == staged_id
        assert list_payload["items"][0]["access_count"] == 4

        discard_resp = client.post(
            "/staging/discard",
            json={
                "session_id": "sess-1",
                "staging_ids": [staged_id],
                "discard_reason": "test_cleanup",
            },
        )
        assert discard_resp.status_code == 200
        discard_payload = discard_resp.json()
        assert len(discard_payload["items"]) == 1
        assert discard_payload["status"]["metadata"]["discard_reason"] == "test_cleanup"

        empty_list_resp = client.post(
            "/staging/list",
            json={"session_id": "sess-1", "status": "staged", "limit": 10},
        )
        assert empty_list_resp.status_code == 200
        assert empty_list_resp.json()["items"] == []


def test_dreaming_manual_mode_and_proposals() -> None:
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        client.post(
            "/staging/stage",
            json={
                "session_id": "sess-2",
                "run_id": "run-2",
                "content": "candidate memory",
                "source": "unit-test",
                "importance": 0.75,
                "access_count": 3,
                "ttl_seconds": 900,
                "source_ids": ["chat:sess-2:1:user"],
            },
        )

        status_before = client.get("/dreaming/status")
        assert status_before.status_code == 200
        status_before_payload = status_before.json()
        assert status_before_payload["mode"] == "manual_only"
        assert status_before_payload["scheduler_enabled"] is False
        assert status_before_payload["pending_staged_items"] == 1

        run_resp = client.post(
            "/dreaming/run",
            json={
                "trigger": "manual",
                "session_id": "sess-2",
                "dry_run": False,
                "max_items": 5,
                "metadata": {"initiator": "unit-test"},
            },
        )
        assert run_resp.status_code == 200
        run_payload = run_resp.json()
        assert run_payload["trigger"] == "manual"
        assert run_payload["status"]["status"] == "success"
        assert run_payload["summary"]["created_proposals"] == 1
        assert len(run_payload["proposals"]) == 1
        proposal = run_payload["proposals"][0]
        assert proposal["metadata"]["importance"] == 0.75
        assert proposal["metadata"]["access_count"] == 3
        assert proposal["metadata"]["ttl_seconds"] == 900
        assert proposal["metadata"]["source_ids"] == ["chat:sess-2:1:user"]
        assert proposal["evidence"][0]["source"] == "staging_signal"
        assert proposal["evidence"][0]["confidence"] == 0.75
        assert proposal["evidence"][0]["metadata"]["access_count"] == 3

        proposals_resp = client.post(
            "/dreaming/proposals",
            json={"session_id": "sess-2", "decision": "pending", "limit": 20},
        )
        assert proposals_resp.status_code == 200
        proposals_payload = proposals_resp.json()
        assert len(proposals_payload["items"]) == 1
        assert proposals_payload["items"][0]["proposed_status"] == "candidate"
        assert proposals_payload["items"][0]["decision"] == "pending"

        proposal_id = proposals_payload["items"][0]["proposal_id"]
        decision_resp = client.post(
            "/dreaming/proposals/decision",
            json={
                "proposal_id": proposal_id,
                "decision": "approved",
                "decision_reason": "manual review passed",
                "decided_by": "human",
                "metadata": {"decision_at": "2000-01-01T00:00:00+00:00"},
            },
        )
        assert decision_resp.status_code == 200
        decision_payload = decision_resp.json()
        assert decision_payload["status"]["status"] == "success"
        assert decision_payload["item"]["decision"] == "approved"
        assert decision_payload["item"]["metadata"]["decision_at"] != "2000-01-01T00:00:00+00:00"

        status_after = client.get("/dreaming/status")
        assert status_after.status_code == 200
        status_after_payload = status_after.json()
        assert status_after_payload["last_run_id"]
        assert status_after_payload["last_run_state"] == "completed"
        assert status_after_payload["pending_proposals"] == 0


def test_staging_consolidate_endpoint_runs_dreaming_flow() -> None:
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        stage_resp = client.post(
            "/staging/stage",
            json={
                "session_id": "sess-consolidate",
                "run_id": "run-consolidate",
                "content": "consolidate this item",
                "source": "unit-test",
            },
        )
        assert stage_resp.status_code == 200

        consolidate_resp = client.post(
            "/staging/consolidate",
            json={
                "session_id": "sess-consolidate",
                "trigger": "manual",
                "dry_run": False,
                "max_items": 5,
            },
        )
        assert consolidate_resp.status_code == 200
        consolidate_payload = consolidate_resp.json()
        assert consolidate_payload["status"]["status"] == "success"
        assert consolidate_payload["summary"]["created_proposals"] == 1
        assert len(consolidate_payload["proposals"]) == 1


def test_dreaming_can_create_session_summary_proposal() -> None:
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        client.post(
            "/history/append",
            json={"session_id": "sess-summary", "role": "user", "content": "We should preserve Dreaming as a controlled proposal flow."},
        )
        client.post(
            "/history/append",
            json={"session_id": "sess-summary", "role": "assistant", "content": "Dreaming should not directly promote facts without a decision."},
        )

        run_resp = client.post(
            "/dreaming/run",
            json={
                "trigger": "manual",
                "session_id": "sess-summary",
                "dry_run": False,
                "include_session_summary": True,
                "summary_max_messages": 10,
                "summary_max_chars": 800,
            },
        )

        assert run_resp.status_code == 200
        payload = run_resp.json()
        assert payload["summary"]["created_proposals"] == 1
        proposal = payload["proposals"][0]
        assert proposal["staging_id"] is None
        assert proposal["target_key"].startswith("session_summary:sess-summary:")
        assert proposal["promotion_reason"] == "manual dreaming session summary proposal"
        assert proposal["evidence"][0]["source"] == "session_summary"
        assert proposal["evidence"][0]["metadata"]["message_count"] == 2
        assert "controlled proposal flow" in proposal["proposed_value"]
        assert proposal["decision"] == "pending"


def test_dreaming_attaches_only_direct_accepted_relation_evidence() -> None:
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        stage_resp = client.post(
            "/staging/stage",
            json={
                "session_id": "sess-relations",
                "content": "A candidate grounded in one known message.",
                "source_ids": ["message:known"],
            },
        )
        assert stage_resp.status_code == 200

        related_resp = client.post(
            "/relations/upsert",
            json={
                "session_id": "sess-relations",
                "source": "message:known",
                "relation": "SUPPORTS",
                "target": "fact:grounded",
                "validated": True,
                "weight": 0.9,
            },
        )
        assert related_resp.status_code == 200
        unrelated_resp = client.post(
            "/relations/upsert",
            json={
                "session_id": "sess-relations",
                "source": "message:other",
                "relation": "SUPPORTS",
                "target": "fact:unrelated",
                "explicit_acceptance": True,
            },
        )
        assert unrelated_resp.status_code == 200

        run_resp = client.post(
            "/dreaming/run",
            json={
                "session_id": "sess-relations",
                "include_relation_evidence": True,
                "include_quality_signals": True,
                "relation_limit": 10,
            },
        )
        assert run_resp.status_code == 200
        payload = run_resp.json()
        proposal = payload["proposals"][0]
        graph_evidence = [item for item in proposal["evidence"] if item["source"] == "graph_relation"]

        assert len(graph_evidence) == 1
        assert graph_evidence[0]["reference"] == "message:known -> SUPPORTS -> fact:grounded"
        assert graph_evidence[0]["confidence"] == 0.9
        assert proposal["metadata"]["relation_evidence_count"] == 1
        assert payload["summary"]["relation_evidence"]["attached"] == 1
        quality = next(item for item in proposal["evidence"] if item["source"] == "proposal_quality_signals")
        assert quality["confidence"] is None
        assert quality["metadata"]["interpretation"] == "validator_evidence_only"
        assert quality["metadata"]["complexity"]["accepted_relation_count"] == 1
        assert quality["metadata"]["coverage"]["source_coverage_ratio"] == 1.0
        assert quality["metadata"]["coverage"]["relation_coverage_ratio"] == 1.0
        assert proposal["decision"] == "pending"
        assert payload["summary"]["quality_signals"] == {"enabled": True, "attached": 1}

        expanded = client.post(
            "/relations/expand",
            json={"session_id": "sess-relations", "limit": 10},
        )
        assert expanded.status_code == 200
        assert len(expanded.json()["items"]) == 2


def test_assurance_required_proposal_needs_bound_strict_validator_report(monkeypatch) -> None:
    monkeypatch.setenv("LIARA_VALIDATOR_ASYNC", "0")
    monkeypatch.setattr(
        memory_store_module,
        "_execute_validator_job",
        lambda **kwargs: {
            "state": "completed",
            "summary": {"job_id": kwargs.get("job_id"), "exit_code": 0, "findings_count": 0},
            "findings": [],
            "artifacts": [f"artifacts/validator_jobs/{kwargs.get('job_id')}/report.json"],
        },
    )
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        client.post(
            "/staging/stage",
            json={"session_id": "sess-assurance", "content": "candidate requiring assurance"},
        )
        run_resp = client.post(
            "/dreaming/run",
            json={
                "session_id": "sess-assurance",
                "include_quality_signals": True,
                "require_assurance_for_approval": True,
            },
        )
        proposal_id = run_resp.json()["proposals"][0]["proposal_id"]
        assert any(
            item["source"] == "proposal_quality_signals"
            for item in run_resp.json()["proposals"][0]["evidence"]
        )

        blocked = client.post(
            "/dreaming/proposals/decision",
            json={
                "proposal_id": proposal_id,
                "decision": "approved",
                "decision_reason": "approval attempted before assurance",
            },
        )
        assert blocked.json()["status"]["error"] == "proposal_assurance_not_passed"

        mismatched_submit = client.post(
            "/validator/submit",
            json={
                "workspace": "c:/ai/LIARA",
                "strict_mode": True,
                "context": "dreaming_proposal_assurance",
                "proposal_id": "another-proposal",
            },
        )
        mismatched_attach = client.post(
            "/dreaming/proposals/assurance",
            json={
                "proposal_id": proposal_id,
                "validator_job_id": mismatched_submit.json()["job_id"],
                "assessment_reason": "must reject mismatched subject",
            },
        )
        assert mismatched_attach.json()["status"]["error"] == "validator_subject_mismatch"

        non_strict_submit = client.post(
            "/validator/submit",
            json={
                "workspace": "c:/ai/LIARA",
                "context": "dreaming_proposal_assurance",
                "proposal_id": proposal_id,
            },
        )
        non_strict_attach = client.post(
            "/dreaming/proposals/assurance",
            json={
                "proposal_id": proposal_id,
                "validator_job_id": non_strict_submit.json()["job_id"],
                "assessment_reason": "non-strict report is informative only",
            },
        )
        assert non_strict_attach.json()["verdict"] == "attention"
        still_blocked = client.post(
            "/dreaming/proposals/decision",
            json={
                "proposal_id": proposal_id,
                "decision": "approved",
                "decision_reason": "non-strict assurance must not pass",
            },
        )
        assert still_blocked.json()["status"]["error"] == "proposal_assurance_not_passed"

        submit = client.post(
            "/validator/submit",
            json={
                "workspace": "c:/ai/LIARA",
                "scope": "validate",
                "strict_mode": True,
                "context": "dreaming_proposal_assurance",
                "proposal_id": proposal_id,
            },
        )
        assert submit.json()["subject"]["proposal_id"] == proposal_id
        assert len(submit.json()["subject"]["proposal_digest"]) == 64
        job_id = submit.json()["job_id"]

        attached = client.post(
            "/dreaming/proposals/assurance",
            json={
                "proposal_id": proposal_id,
                "validator_job_id": job_id,
                "assessment_reason": "strict proposal-scoped validator report",
            },
        )
        attached_payload = attached.json()
        assert attached_payload["status"]["status"] == "success"
        assert attached_payload["verdict"] == "passed"
        assert attached_payload["item"]["metadata"]["assurance_job_id"] == job_id
        assert any(item["source"] == "validator_report" for item in attached_payload["item"]["evidence"])

        approved = client.post(
            "/dreaming/proposals/decision",
            json={
                "proposal_id": proposal_id,
                "decision": "approved",
                "decision_reason": "bound assurance report passed",
            },
        )
        assert approved.json()["status"]["status"] == "success"
        assert approved.json()["item"]["decision"] == "approved"


def test_dreaming_cleanup_is_preview_first_and_preserves_protected_state() -> None:
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)
    now = datetime.now(UTC)

    with TestClient(app) as client:
        expired = client.post(
            "/staging/stage",
            json={"session_id": "sess-cleanup", "content": "expired", "ttl_seconds": 10},
        ).json()["items"][0]
        protected = client.post(
            "/staging/stage",
            json={"session_id": "sess-cleanup", "content": "protected", "ttl_seconds": 10},
        ).json()["items"][0]

        store._dreaming_proposals.extend(
            [
                MemoryDreamingProposalRecord(
                    proposal_id="proposal-protected",
                    session_id="sess-cleanup",
                    staging_id=protected["staging_id"],
                    target_namespace="dreaming",
                    target_key="protected",
                    proposed_value="protected",
                    promotion_reason="pending proposal protects staging",
                    decision="pending",
                    created_at=now.isoformat(),
                ),
                MemoryDreamingProposalRecord(
                    proposal_id="proposal-rejected-old",
                    session_id="sess-cleanup",
                    target_namespace="dreaming",
                    target_key="rejected-old",
                    proposed_value="old",
                    promotion_reason="cleanup candidate",
                    decision="rejected",
                    created_at=(now - timedelta(days=2)).isoformat(),
                    metadata={"decision_at": (now - timedelta(hours=2)).isoformat()},
                ),
                MemoryDreamingProposalRecord(
                    proposal_id="proposal-approved-old",
                    session_id="sess-cleanup",
                    target_namespace="dreaming",
                    target_key="approved-old",
                    proposed_value="keep",
                    promotion_reason="approved provenance",
                    decision="approved",
                    created_at=(now - timedelta(days=2)).isoformat(),
                    metadata={"decision_at": (now - timedelta(hours=2)).isoformat()},
                ),
                MemoryDreamingProposalRecord(
                    proposal_id="proposal-rejected-legacy",
                    session_id="sess-cleanup",
                    target_namespace="dreaming",
                    target_key="legacy",
                    proposed_value="keep",
                    promotion_reason="legacy without decision timestamp",
                    decision="rejected",
                    created_at=(now - timedelta(days=2)).isoformat(),
                ),
            ]
        )

        cleanup_payload = {
            "session_id": "sess-cleanup",
            "now_ts": (now + timedelta(seconds=20)).timestamp(),
            "rejected_retention_seconds": 3600,
        }
        preview = client.post("/dreaming/cleanup", json=cleanup_payload).json()
        assert preview["dry_run"] is True
        assert preview["staging_ids"] == [expired["staging_id"]]
        assert preview["proposal_ids"] == ["proposal-rejected-old"]
        assert preview["staging_removed"] == 0
        assert preview["proposals_removed"] == 0

        applied = client.post("/dreaming/cleanup", json={**cleanup_payload, "dry_run": False}).json()
        assert applied["staging_removed"] == 1
        assert applied["proposals_removed"] == 1

        remaining_staging = client.post(
            "/staging/list",
            json={"session_id": "sess-cleanup", "limit": 20},
        ).json()["items"]
        assert [item["staging_id"] for item in remaining_staging] == [protected["staging_id"]]
        remaining_proposals = client.post(
            "/dreaming/proposals",
            json={"session_id": "sess-cleanup", "decision": "all", "limit": 20},
        ).json()["items"]
        remaining_ids = {item["proposal_id"] for item in remaining_proposals}
        assert remaining_ids == {
            "proposal-protected",
            "proposal-approved-old",
            "proposal-rejected-legacy",
        }


def test_dreaming_does_not_auto_verify() -> None:
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        stage_resp = client.post(
            "/staging/stage",
            json={
                "session_id": "sess-no-auto-verify",
                "run_id": "run-no-auto-verify",
                "content": "possible memory candidate",
                "source": "unit-test",
            },
        )
        assert stage_resp.status_code == 200

        consolidate_resp = client.post(
            "/staging/consolidate",
            json={
                "session_id": "sess-no-auto-verify",
                "trigger": "manual",
                "dry_run": False,
                "max_items": 5,
            },
        )
        assert consolidate_resp.status_code == 200
        payload = consolidate_resp.json()
        assert payload["status"]["status"] == "success"
        assert payload["summary"]["created_proposals"] == 1
        assert all(item["proposed_status"] == "candidate" for item in payload["proposals"])
        assert all(item["proposed_status"] != "verified" for item in payload["proposals"])


def test_validator_submit_status_result_roundtrip(monkeypatch) -> None:
    monkeypatch.setenv("LIARA_VALIDATOR_ASYNC", "0")

    def _fake_execute_validator_job(
        *,
        job_id: str,
        workspace: str,
        scope: str,
        checks: list[str],
        strict_mode: bool,
        session_id: str | None = None,
        request_id: str | None = None,
        run_id: str | None = None,
        source: str | None = None,
    ):
        del checks
        del session_id
        del request_id
        del run_id
        del source
        return {
            "state": "completed",
            "summary": {
                "execution_mode": "docker_compose",
                "job_id": job_id,
                "workspace": workspace,
                "scope": scope,
                "strict_mode": strict_mode,
                "exit_code": 0,
                "findings_count": 0,
            },
            "findings": [],
            "artifacts": [f"artifacts/validator_jobs/{job_id}/run.log"],
        }

    monkeypatch.setattr(memory_store_module, "_execute_validator_job", _fake_execute_validator_job)
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        submit_resp = client.post(
            "/validator/submit",
            json={
                "workspace": "c:/ai/LIARA/workers/ai-validator",
                "scope": "quick",
                "checks": ["lint", "tests"],
                "strict_mode": True,
                "request_id": "req-validator-1",
                "run_id": "run-validator-1",
                "session_id": "sess-validator-1",
                "source": "orchestrator",
                "context": "memory.validator.submit.test",
            },
        )
        assert submit_resp.status_code == 200
        submit_payload = submit_resp.json()
        assert submit_payload["status"]["status"] == "success"
        assert submit_payload["state"] == "completed"
        assert submit_payload["summary"]["execution_mode"] == "docker_compose"

        job_id = submit_payload["job_id"]
        status_resp = client.post("/validator/status", json={"job_id": job_id})
        assert status_resp.status_code == 200
        status_payload = status_resp.json()
        assert status_payload["status"]["status"] == "success"
        assert status_payload["state"] == "completed"

        result_resp = client.post("/validator/result", json={"job_id": job_id})
        assert result_resp.status_code == 200
        result_payload = result_resp.json()
        assert result_payload["status"]["status"] == "success"
        assert result_payload["state"] == "completed"
        assert result_payload["findings"] == []
        assert len(result_payload["artifacts"]) == 1
        assert result_payload["artifacts"][0].endswith("/run.log") or result_payload["artifacts"][0].endswith("\\run.log")


def test_validator_submit_emits_traceable_audit_event(monkeypatch) -> None:
    monkeypatch.setenv("LIARA_VALIDATOR_ASYNC", "0")
    executed_calls: list[dict[str, Any]] = []

    def _capture_executed(*args, **kwargs):
        del args
        executed_calls.append(kwargs)
        return None

    monkeypatch.setattr(memory_store_module, "_memory_audit_log_executed", _capture_executed)
    monkeypatch.setattr(
        memory_store_module,
        "_execute_validator_job",
        lambda **kwargs: {
            "state": "completed",
            "summary": {
                "execution_mode": "docker_compose",
                "job_id": kwargs.get("job_id"),
                "exit_code": 0,
                "findings_count": 0,
            },
            "findings": [],
            "artifacts": [f"artifacts/validator_jobs/{kwargs.get('job_id')}/run.log"],
        },
    )
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        response = client.post(
            "/validator/submit",
            json={
                "workspace": "c:/ai/LIARA/workers/ai-validator",
                "scope": "validate",
                "request_id": "req-validator-audit-1",
                "run_id": "run-validator-audit-1",
                "session_id": "sess-validator-audit-1",
                "source": "orchestrator",
                "context": "memory.validator.submit.audit",
            },
        )
        assert response.status_code == 200

    assert len(executed_calls) >= 1
    last_call = executed_calls[-1]
    assert last_call["command"] == "memory"
    assert "validator_submit" in last_call["args"]
    assert last_call["request_id"] == "req-validator-audit-1"
    assert last_call["run_id"] == "run-validator-audit-1"
    assert last_call["session_id"] == "sess-validator-audit-1"
    assert last_call["source"] == "orchestrator"
    assert last_call["context"] == "memory.validator.submit.audit"


def test_validator_submit_mock_mode_without_worker_dependency(monkeypatch) -> None:
    monkeypatch.setenv("LIARA_VALIDATOR_ASYNC", "0")
    monkeypatch.setenv("LIARA_VALIDATOR_EXECUTION_MODE", "mock")

    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        submit_resp = client.post(
            "/validator/submit",
            json={
                "workspace": "c:/ai/LIARA/workers/ai-validator",
                "scope": "validate",
                "checks": ["lint", "tests"],
            },
        )
        assert submit_resp.status_code == 200
        submit_payload = submit_resp.json()
        assert submit_payload["state"] == "completed"
        assert submit_payload["summary"]["execution_mode"] == "mock"

        status_resp = client.post("/validator/status", json={"job_id": submit_payload["job_id"]})
        assert status_resp.status_code == 200
        assert status_resp.json()["state"] == "completed"


def test_update_creates_new_version() -> None:
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        first_resp = client.post(
            "/facts/upsert",
            json={
                "namespace": "profile",
                "key": "city",
                "value": "Berlin",
                "source": "unit-test",
                "status": "candidate",
                "promotion_reason": "initial capture",
            },
        )
        assert first_resp.status_code == 200
        first_item = first_resp.json()["items"][0]

        second_resp = client.post(
            "/facts/upsert",
            json={
                "namespace": "profile",
                "key": "city",
                "value": "Munich",
                "source": "unit-test",
                "status": "candidate",
                "promotion_reason": "corrected value",
            },
        )
        assert second_resp.status_code == 200
        second_item = second_resp.json()["items"][0]

        assert first_item["fact_id"] != second_item["fact_id"]
        assert first_item["metadata"]["version"] == 1
        assert second_item["metadata"]["version"] == 2
        assert second_item["metadata"]["previous_fact_id"] == first_item["fact_id"]


def test_dreaming_proposal_decision_blocks_verified_approval_for_agent() -> None:
    store = InMemoryMemoryServiceStore()
    proposal = MemoryDreamingProposalRecord(
        proposal_id="prop-verified-1",
        session_id="sess-verified",
        staging_id="stg-verified-1",
        target_namespace="facts",
        target_key="profile:truth",
        proposed_value="trusted value",
        proposed_status=MemoryLifecycleStatus.verified,
        promotion_reason="test gate",
        created_at="2026-07-13T10:00:00Z",
    )
    store._dreaming_proposals.append(proposal)
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        response = client.post(
            "/dreaming/proposals/decision",
            json={
                "proposal_id": "prop-verified-1",
                "decision": "approved",
                "decision_reason": "agent says ok",
                "decided_by": "agent",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"]["status"] == "failed"
        assert payload["status"]["error"] == "verified_requires_human_gate"


def test_backed_store_persists_staging_and_proposals(monkeypatch) -> None:
    monkeypatch.setenv("LIARA_VALIDATOR_ASYNC", "0")
    monkeypatch.setattr(Settings, "QDRANT_URL", "")
    monkeypatch.setattr(Settings, "CHROMA_HOST", "")
    monkeypatch.setattr(Settings, "NEO4J_URL", "")
    monkeypatch.setattr(
        memory_store_module,
        "_execute_validator_job",
        lambda **kwargs: {
            "state": "completed",
            "summary": {"job_id": kwargs.get("job_id"), "exit_code": 0, "findings_count": 0},
            "findings": [],
            "artifacts": [f"artifacts/validator_jobs/{kwargs.get('job_id')}/report.json"],
        },
    )

    session_store = _StubAsyncKVStore()
    fact_store = _StubAsyncKVStore()
    store = BackedMemoryServiceStore(
        session_store=session_store,
        fact_store=fact_store,
        retrieval_index=None,
        context_store=None,
        graph_store=None,
    )
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        stage_resp = client.post(
            "/staging/stage",
            json={
                "session_id": "sess-backed",
                "run_id": "run-backed",
                "content": "persist me",
                "source": "unit-test",
                "importance": 0.65,
                "ttl_seconds": 10,
                "source_ids": ["message:backed-source"],
            },
        )
        assert stage_resp.status_code == 200
        stage_payload = stage_resp.json()
        staged_id = stage_payload["items"][0]["staging_id"]
        assert stage_payload["status"]["backend"] == "postgres"

        touch_resp = client.post(
            "/staging/touch",
            json={
                "session_id": "sess-backed",
                "staging_ids": [staged_id],
                "touch_reason": "backed-store recall",
            },
        )
        assert touch_resp.status_code == 200
        touch_payload = touch_resp.json()
        assert touch_payload["status"]["backend"] == "postgres"
        assert touch_payload["items"][0]["access_count"] == 1

        relation_resp = client.post(
            "/relations/upsert",
            json={
                "session_id": "sess-backed",
                "source": "message:backed-source",
                "relation": "SUPPORTS",
                "target": "fact:backed-target",
                "validated": True,
                "weight": 0.8,
            },
        )
        assert relation_resp.status_code == 200

        client.post(
            "/history/append",
            json={"session_id": "sess-backed", "role": "user", "content": "Backed store summary input."},
        )
        client.post(
            "/history/append",
            json={"session_id": "sess-backed", "role": "assistant", "content": "Summary should become a pending proposal."},
        )

        run_resp = client.post(
            "/dreaming/run",
            json={
                "trigger": "manual",
                "session_id": "sess-backed",
                "dry_run": False,
                "include_session_summary": True,
                "include_relation_evidence": True,
                "include_quality_signals": True,
                "require_assurance_for_approval": True,
            },
        )
        assert run_resp.status_code == 200
        run_payload = run_resp.json()
        assert run_payload["status"]["backend"] == "postgres"
        assert run_payload["summary"]["created_proposals"] == 2

        proposals_resp = client.post(
            "/dreaming/proposals",
            json={"session_id": "sess-backed", "decision": "pending", "limit": 10},
        )
        assert proposals_resp.status_code == 200
        proposals_payload = proposals_resp.json()
        assert len(proposals_payload["items"]) == 2
        assert proposals_payload["status"]["backend"] == "postgres"
        staged_proposal = next(item for item in proposals_payload["items"] if item["staging_id"] == staged_id)
        summary_proposal = next(item for item in proposals_payload["items"] if item["staging_id"] is None)
        assert staged_proposal["metadata"]["importance"] == 0.65
        assert staged_proposal["metadata"]["access_count"] == 1
        assert staged_proposal["metadata"]["relation_evidence_count"] == 1
        assert any(item["source"] == "graph_relation" for item in staged_proposal["evidence"])
        staged_quality = next(
            item for item in staged_proposal["evidence"] if item["source"] == "proposal_quality_signals"
        )
        assert staged_quality["metadata"]["coverage"]["source_coverage_ratio"] == 1.0
        assert staged_quality["metadata"]["coverage"]["relation_coverage_ratio"] == 1.0
        assert summary_proposal["evidence"][0]["source"] == "session_summary"
        assert summary_proposal["metadata"]["summary_message_count"] == 2
        assert any(item["source"] == "proposal_quality_signals" for item in summary_proposal["evidence"])
        assert run_payload["summary"]["quality_signals"] == {"enabled": True, "attached": 2}

        proposal_id = staged_proposal["proposal_id"]
        validator_submit = client.post(
            "/validator/submit",
            json={
                "workspace": "c:/ai/LIARA",
                "scope": "validate",
                "strict_mode": True,
                "context": "dreaming_proposal_assurance",
                "proposal_id": proposal_id,
            },
        )
        assert validator_submit.status_code == 200
        validator_job_id = validator_submit.json()["job_id"]
        assurance_resp = client.post(
            "/dreaming/proposals/assurance",
            json={
                "proposal_id": proposal_id,
                "validator_job_id": validator_job_id,
                "assessment_reason": "backed-store assurance",
            },
        )
        assert assurance_resp.status_code == 200
        assert assurance_resp.json()["verdict"] == "passed"

        decision_resp = client.post(
            "/dreaming/proposals/decision",
            json={
                "proposal_id": proposal_id,
                "decision": "rejected",
                "decision_reason": "operator rejected",
                "decided_by": "human",
            },
        )
        assert decision_resp.status_code == 200
        decision_payload = decision_resp.json()
        assert decision_payload["status"]["status"] == "success"
        assert decision_payload["item"]["decision"] == "rejected"
        assert decision_payload["item"]["metadata"]["decision_at"]

        assert any(key.startswith("staging_record:") for key in fact_store.data)
        assert any(key.startswith("dreaming_proposal:") for key in fact_store.data)

        cleanup_resp = client.post(
            "/dreaming/cleanup",
            json={
                "session_id": "sess-backed",
                "dry_run": False,
                "now_ts": (datetime.now(UTC) + timedelta(hours=2)).timestamp(),
                "rejected_retention_seconds": 3600,
            },
        )
        assert cleanup_resp.status_code == 200
        cleanup_payload = cleanup_resp.json()
        assert cleanup_payload["status"]["backend"] == "postgres"
        assert cleanup_payload["staging_removed"] == 1
        assert cleanup_payload["proposals_removed"] == 1
        assert not any(key.startswith("staging_record:") for key in fact_store.data)
        assert f"dreaming_proposal:{proposal_id}" not in fact_store.data


def test_backed_store_persists_validator_jobs(monkeypatch) -> None:
    monkeypatch.setenv("LIARA_VALIDATOR_ASYNC", "0")
    monkeypatch.setattr(Settings, "QDRANT_URL", "")
    monkeypatch.setattr(Settings, "CHROMA_HOST", "")
    monkeypatch.setattr(Settings, "NEO4J_URL", "")
    monkeypatch.setattr(
        memory_store_module,
        "_execute_validator_job",
        lambda **kwargs: {
            "state": "completed",
            "summary": {
                "execution_mode": "docker_compose",
                "job_id": kwargs.get("job_id"),
                "exit_code": 0,
                "findings_count": 0,
            },
            "findings": [],
            "artifacts": [f"artifacts/validator_jobs/{kwargs.get('job_id')}/run.log"],
        },
    )

    session_store = _StubAsyncKVStore()
    fact_store = _StubAsyncKVStore()
    store = BackedMemoryServiceStore(
        session_store=session_store,
        fact_store=fact_store,
        retrieval_index=None,
        context_store=None,
        graph_store=None,
    )
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        submit_resp = client.post(
            "/validator/submit",
            json={
                "workspace": "c:/ai/LIARA/workers/ai-validator",
                "scope": "security",
                "request_id": "req-validator-backed-1",
            },
        )
        assert submit_resp.status_code == 200
        submit_payload = submit_resp.json()
        job_id = submit_payload["job_id"]
        assert submit_payload["status"]["backend"] == "postgres"

        status_resp = client.post("/validator/status", json={"job_id": job_id})
        assert status_resp.status_code == 200
        assert status_resp.json()["status"]["backend"] == "postgres"

        result_resp = client.post("/validator/result", json={"job_id": job_id})
        assert result_resp.status_code == 200
        assert result_resp.json()["status"]["backend"] == "postgres"

        assert any(key.startswith("validator_job:") for key in fact_store.data)


def test_staging_mutation_emits_audit_event(monkeypatch) -> None:
    executed_calls: list[dict[str, Any]] = []

    def _capture_executed(*args, **kwargs):
        del args
        executed_calls.append(kwargs)
        return None

    monkeypatch.setattr(memory_store_module, "_memory_audit_log_executed", _capture_executed)
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        response = client.post(
            "/staging/stage",
            json={
                "session_id": "sess-audit-1",
                "run_id": "run-audit-1",
                "content": "audit me",
                "source": "api",
                "metadata": {
                    "request_id": "req-audit-1",
                    "context": "memory.staging.stage.test",
                },
            },
        )
        assert response.status_code == 200

    assert len(executed_calls) >= 1
    last_call = executed_calls[-1]
    assert last_call["command"] == "memory"
    assert "staging_stage" in last_call["args"]
    assert last_call["request_id"] == "req-audit-1"
    assert last_call["session_id"] == "sess-audit-1"


def test_audit_append_only(monkeypatch) -> None:
    executed_calls: list[dict[str, Any]] = []

    def _capture_executed(*args, **kwargs):
        del args
        executed_calls.append(kwargs)
        return None

    monkeypatch.setattr(memory_store_module, "_memory_audit_log_executed", _capture_executed)
    store = InMemoryMemoryServiceStore()
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        stage_resp = client.post(
            "/staging/stage",
            json={
                "session_id": "sess-audit-append",
                "run_id": "run-audit-append",
                "content": "append entry 1",
                "source": "api",
                "metadata": {
                    "request_id": "req-audit-append-1",
                    "context": "memory.staging.stage.append",
                },
            },
        )
        assert stage_resp.status_code == 200
        staged_id = stage_resp.json()["items"][0]["staging_id"]

        discard_resp = client.post(
            "/staging/discard",
            json={
                "session_id": "sess-audit-append",
                "staging_ids": [staged_id],
                "discard_reason": "append_only_contract",
                "metadata": {
                    "request_id": "req-audit-append-2",
                    "context": "memory.staging.discard.append",
                },
            },
        )
        assert discard_resp.status_code == 200

    assert len(executed_calls) >= 2
    assert executed_calls[-2]["write_mode"] == "append"
    assert executed_calls[-1]["write_mode"] == "append"
    assert executed_calls[-2]["request_id"] == "req-audit-append-1"
    assert executed_calls[-1]["request_id"] == "req-audit-append-2"
    assert "staging_stage" in executed_calls[-2]["args"]
    assert "staging_discard" in executed_calls[-1]["args"]


def test_denied_verified_decision_emits_blocked_audit(monkeypatch) -> None:
    blocked_calls: list[dict[str, Any]] = []

    def _capture_blocked(*args, **kwargs):
        del args
        blocked_calls.append(kwargs)
        return None

    monkeypatch.setattr(memory_store_module, "_memory_audit_log_blocked", _capture_blocked)
    store = InMemoryMemoryServiceStore()
    store._dreaming_proposals.append(
        MemoryDreamingProposalRecord(
            proposal_id="prop-audit-verified-1",
            session_id="sess-audit-2",
            staging_id="stg-audit-2",
            target_namespace="facts",
            target_key="profile:verified",
            proposed_value="value",
            proposed_status=MemoryLifecycleStatus.verified,
            promotion_reason="gate-test",
            created_at="2026-07-13T12:00:00Z",
        )
    )
    app = create_memory_service_app(store)

    with TestClient(app) as client:
        response = client.post(
            "/dreaming/proposals/decision",
            json={
                "proposal_id": "prop-audit-verified-1",
                "decision": "approved",
                "decision_reason": "agent denied path",
                "decided_by": "agent",
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"]["error"] == "verified_requires_human_gate"

    assert len(blocked_calls) >= 1
    last_call = blocked_calls[-1]
    assert last_call["command"] == "memory"
    assert last_call["reason"] == "verified_requires_human_gate"
    assert "dreaming_decide_proposal" in last_call["args"]
    assert last_call["session_id"] == "sess-audit-2"
