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


def test_judge_failure_fails_closed_without_leaking_error(client_for, audit_log):
    class _BrokenJudge:
        def evaluate_pre_action(self, context):
            raise RuntimeError("judge down at /secret/path")

    resp = client_for(_BrokenJudge()).post("/compute/generate", json=_BODY)

    assert resp.status_code == 403
    assert "secret" not in resp.text
    assert _FakeTool.calls == []
    assert [entry["decision"] for entry in audit_log] == ["block"]


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
