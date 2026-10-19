"""POST /compute/generate goes through the verified principal and the pre-action judge."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.api.exceptions import UnauthorizedPrincipalError
from services.api.routers import compute as compute_router
from services.config import Settings
from services.judge.contracts import JudgeDecision

_BODY = {
    "model_name": "wind_power",
    "description": "power from wind speed",
    "inputs": {"wind_speed": "float"},
    "outputs": {"power_kw": "float"},
}


class _FakeJudge:
    def __init__(self, decision):
        self.decision = decision
        self.contexts = []

    def evaluate_pre_action(self, context):
        self.contexts.append(context)
        return self.decision


class _FakeTool:
    calls = []

    async def execute(self, **kwargs):
        _FakeTool.calls.append(kwargs)
        return {"status": "success", "model_name": kwargs["model_name"]}


@pytest.fixture
def audit_log(monkeypatch):
    entries = []
    monkeypatch.setattr(compute_router, "log_judge_pre_action", lambda **kwargs: entries.append(kwargs))
    return entries


@pytest.fixture
def client_for(monkeypatch, audit_log):
    _FakeTool.calls = []
    monkeypatch.setattr(compute_router, "ComputeGenerateTool", _FakeTool)
    # Development auth unless a test opts into fail-closed explicitly.
    monkeypatch.delenv("LIARA_ENV", raising=False)
    monkeypatch.setenv("LIARA_FAIL_CLOSED_AUTH", "false")
    monkeypatch.setattr(Settings, "LIARA_FAIL_CLOSED_AUTH", False)
    for key, value in (("LIARA_ADMIN_TOKEN", "test-compute-admin"), ("LIARA_USER_TOKEN", "test-compute-user")):
        monkeypatch.setenv(key, value)
        monkeypatch.setattr(Settings, key, value)

    def build(judge):
        app = FastAPI()
        # services.api.app maps this to 401 the same way; importing it here
        # would build the default memory store.
        app.add_exception_handler(
            UnauthorizedPrincipalError,
            lambda request, exc: JSONResponse(status_code=401, content={"detail": str(exc)}),
        )
        app.state.orchestrator = SimpleNamespace(judge_engine=judge)
        app.include_router(compute_router.router)
        return TestClient(app)

    return build


def test_judge_block_stops_generation(client_for, audit_log):
    judge = _FakeJudge(JudgeDecision.block(issues=["Model name is empty."]))
    resp = client_for(judge).post("/compute/generate", json=_BODY)

    assert resp.status_code == 403
    assert resp.json()["detail"]["decision"] == "block"
    assert _FakeTool.calls == []
    (entry,) = audit_log
    assert entry["tool_name"] == "compute.generate"
    assert entry["decision"] == "block"
    assert entry["issues"] == ["Model name is empty."]


def test_judge_allow_runs_generation_with_verified_actor(client_for):
    judge = _FakeJudge(JudgeDecision.allow())
    resp = client_for(judge).post("/compute/generate", json=_BODY, headers={"X-LIARA-Actor-ID": "tester"})

    assert resp.status_code == 200
    assert _FakeTool.calls == [_BODY]
    (context,) = judge.contexts
    assert context.action == "compute.generate"
    assert context.actor == "tester"
    assert context.input == _BODY


def test_judge_failure_fails_closed_without_leaking_error(client_for, audit_log, caplog):
    class _BrokenJudge:
        def evaluate_pre_action(self, context):
            raise RuntimeError("judge down at /secret/path")

    resp = client_for(_BrokenJudge()).post("/compute/generate", json=_BODY)

    assert resp.status_code == 403
    assert "secret" not in resp.text
    assert "/secret/path" not in caplog.text
    assert _FakeTool.calls == []
    assert [entry["decision"] for entry in audit_log] == ["block"]
    assert resp.json()["detail"]["reason_code"] == "judge.pre_action_failed"
    assert resp.json()["detail"]["request_id"] == audit_log[0]["request_id"]


def test_missing_credentials_rejected_when_fail_closed(client_for, monkeypatch):
    monkeypatch.setenv("LIARA_FAIL_CLOSED_AUTH", "true")
    monkeypatch.setattr(Settings, "LIARA_FAIL_CLOSED_AUTH", True)
    judge = _FakeJudge(JudgeDecision.allow())
    resp = client_for(judge).post("/compute/generate", json=_BODY)

    assert resp.status_code == 401
    assert judge.contexts == []
    assert _FakeTool.calls == []


def test_non_object_body_rejected(client_for):
    resp = client_for(_FakeJudge(JudgeDecision.allow())).post("/compute/generate", json=["model_name"])

    assert resp.status_code == 422
    assert _FakeTool.calls == []


@pytest.mark.parametrize("decision,status", [
    (JudgeDecision.revise(), 403), (JudgeDecision.warn(), 200), (JudgeDecision.allow(), 200),
])
def test_decisions_are_audited_before_execution(client_for, audit_log, decision, status):
    resp = client_for(_FakeJudge(decision)).post("/compute/generate", json=_BODY)
    assert resp.status_code == status
    assert len(_FakeTool.calls) == (1 if status == 200 else 0)
    (entry,) = audit_log
    assert entry["decision"] == decision.decision.value
    assert entry["context"] == f"api.compute.generate:{decision.decision.value}"
    assert entry["request_id"] == entry["run_id"]


@pytest.mark.parametrize("headers", [{}, {"X-LIARA-Actor-ID": "admin"}, {"Authorization": "Bearer invalid"}])
def test_production_rejects_unverified_credentials(client_for, monkeypatch, audit_log, headers):
    monkeypatch.setenv("LIARA_ENV", "production")
    judge = _FakeJudge(JudgeDecision.allow())
    resp = client_for(judge).post("/compute/generate", json=_BODY, headers=headers)
    assert resp.status_code == 401
    assert not judge.contexts and not _FakeTool.calls and not audit_log


@pytest.mark.parametrize("role", ["user", "admin"])
def test_production_valid_bearer_reaches_judge(client_for, monkeypatch, audit_log, role):
    monkeypatch.setenv("LIARA_ENV", "production")
    judge = _FakeJudge(JudgeDecision.allow())
    resp = client_for(judge).post("/compute/generate", json=_BODY,
                                headers={"Authorization": f"Bearer test-compute-{role}"})
    assert resp.status_code == 200
    assert judge.contexts[0].metadata["principal_source"] == f"bearer_{role}"
    assert len(_FakeTool.calls) == len(audit_log) == 1


def test_audit_failure_never_reaches_tool(client_for, monkeypatch):
    def broken_audit(**kwargs):
        raise RuntimeError("audit unavailable")
    monkeypatch.setattr(compute_router, "log_judge_pre_action", broken_audit)
    resp = client_for(_FakeJudge(JudgeDecision.allow())).post("/compute/generate", json=_BODY)
    assert resp.status_code == 503
    assert resp.json()["detail"]["reason_code"] == "judge.audit_unavailable"
    assert not _FakeTool.calls


def test_shared_audit_receives_correlated_decision(client_for, monkeypatch):
    from services.tools.builtin import sys_audit
    entries = []
    monkeypatch.setattr(sys_audit, "_persist_entry", entries.append)
    monkeypatch.setattr(compute_router, "log_judge_pre_action", sys_audit.log_judge_pre_action)
    judge = _FakeJudge(JudgeDecision.block(issues=["policy denied"]))
    resp = client_for(judge).post("/compute/generate", json=_BODY)
    assert resp.status_code == 403
    (entry,) = entries
    assert entry.command == "judge:compute.generate"
    assert entry.policy_decision == "blocked"
    assert entry.policy_reason == "policy denied"
    assert entry.request_id == entry.run_id == judge.contexts[0].request_id
    assert entry.source == "api"
    assert entry.context == "api.compute.generate:block"
