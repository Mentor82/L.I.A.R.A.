import json

import pytest

from services.contracts import (
    InferenceResult,
    ContextDocument,
    ContextSearchResponse,
    MemoryServiceStatus,
    ToolExecutionResult,
    ValidatorResultResponse,
    ValidatorStatusResponse,
    ValidatorSubmitResponse,
)
from services.orchestrator.workspace_agent import (
    WorkspaceAgent,
    WorkspaceMathBudget,
    WorkspacePlan,
    WorkspaceRunResult,
    WorkspaceStep,
    WorkspaceStepKind,
    WorkspaceStepResult,
    is_complex_workspace_request,
    is_workspace_run_followup,
)
from services.orchestrator.orchestrator import Orchestrator


class FakeInference:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    async def infer(self, request):
        self.requests.append(request)
        return InferenceResult(content=json.dumps(self.payload), provider="test", model="test")


class FailoverInference:
    def __init__(self, payload):
        self.payload = payload
        self.providers = []

    async def infer(self, request):
        self.providers.append(request.provider)
        if len(self.providers) == 1:
            return InferenceResult(
                content="",
                provider=request.provider,
                model="missing",
                status="failed",
                error="OPENVINO model directory not configured",
                stop_reason="error",
            )
        return InferenceResult(content=json.dumps(self.payload), provider=request.provider, model="fallback-model")


class FakeTools:
    def __init__(self, *, verify=True, transient_failures=0):
        self.requests = []
        self.verify = verify
        self.transient_failures = transient_failures

    async def execute_tool(self, request):
        self.requests.append(request)
        mutation = request.parameters["command"] in {"mkdir", "touch", "tee"}
        if mutation and self.transient_failures > 0:
            self.transient_failures -= 1
            return ToolExecutionResult(
                tool_name="sys",
                status="failed",
                output=None,
                error="Command timed out after 30s.",
                metadata={"transient_error": True, "mutation_verified": False},
            )
        return ToolExecutionResult(
            tool_name="sys",
            status="success",
            output="ok",
            metadata={
                "mutation_verified": self.verify if mutation else None,
                "mutation_evidence": {"target_path": request.parameters.get("target_path"), "sha256": "abc"},
            },
        )


class GovernanceBlockedTools:
    def __init__(self):
        self.requests = []

    async def execute_tool(self, request):
        self.requests.append(request)
        return ToolExecutionResult(
            tool_name="sys",
            status="failed",
            output=None,
            error="SYS governance authorization required",
            metadata={
                "governance_required": True,
                "governance_classification": {
                    "command": request.parameters["command"],
                    "requires_governance": True,
                    "risk_level": "high",
                    "reasons": ["mutation"],
                },
            },
        )


class InventoryTools(FakeTools):
    async def execute_tool(self, request):
        if request.parameters["command"] == "find" and request.parameters.get("context") == "agent_workspace_preflight_inventory":
            self.requests.append(request)
            return ToolExecutionResult(
                tool_name="sys",
                status="success",
                output=(
                    "/home/liara/workspace/translation_worker/models.py\n"
                    "/home/liara/workspace/tests/test_worker.py\n"
                    "/home/liara/workspace/.venv/bin/pytest\n"
                ),
            )
        if request.parameters["command"] == "cat" and request.parameters.get("context") == "agent_workspace_preflight_read":
            self.requests.append(request)
            path = request.parameters["args"][0]
            content = (
                "from translation_worker.models import TranslationInput\n"
                if path.endswith("tests/test_worker.py")
                else "class TranslationInput: pass\nclass TranslationOutput: pass\n"
            )
            return ToolExecutionResult(tool_name="sys", status="success", output=content)
        return await super().execute_tool(request)


class FakeMemory:
    def __init__(self):
        self.documents = []

    async def validator_submit(self, request):
        return ValidatorSubmitResponse(
            job_id="job-1",
            state="completed",
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def validator_status(self, request):
        return ValidatorStatusResponse(
            job_id=request.job_id,
            state="completed",
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def validator_result(self, request):
        return ValidatorResultResponse(
            job_id=request.job_id,
            state="completed",
            status=MemoryServiceStatus(status="success", backend="memory-service"),
            summary={"exit_code": 0},
        )

    async def context_upsert(self, request):
        document = ContextDocument(
            document_id=request.document_id,
            content=request.content,
            score=1.0,
            scope=request.scope.model_dump(exclude_none=True),
            metadata=request.effective_metadata(),
        )
        self.documents.append(document)
        return ContextSearchResponse(
            items=[document],
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )

    async def context_search(self, request):
        items = [item for item in self.documents if item.scope.get("session_id") == request.scope.session_id]
        return ContextSearchResponse(
            items=items,
            status=MemoryServiceStatus(status="success", backend="memory-service"),
        )


def _plan():
    return {
        "goal": "create a tiny worker",
        "steps": [
            {"id": "mkdir", "kind": "mkdir", "path": "demo"},
            {
                "id": "write",
                "kind": "write",
                "path": "demo/main.py",
                "content": "print('ok')\n",
                "depends_on": ["mkdir"],
            },
            {
                "id": "run",
                "kind": "run",
                "command": "python3",
                "args": ["-c", "print('test')"],
                "depends_on": ["write"],
            },
        ],
    }


def test_complex_workspace_detection_is_narrow():
    assert is_complex_workspace_request("Lege einen Translator Worker im Workspace an")
    assert is_complex_workspace_request(
        "Repariere im WSL-Workspace die Contract-Abweichung bei leerem Übersetzungstext. "
        "Verwende Pydantic-v2-Validatoren und führe anschließend alle Tests aus."
    )
    assert is_complex_workspace_request("Behebe den fehlgeschlagenen Test im Projektcode")
    assert is_complex_workspace_request("Ändere das Modul im Workspace")
    assert not is_complex_workspace_request("Was ist ein Worker?")
    assert not is_complex_workspace_request("/sys mkdir demo")
    assert is_workspace_run_followup("Okay, was fehlte?")
    assert is_workspace_run_followup("Welche Validator-Findings gab es?")
    assert is_workspace_run_followup("analysiere den Fehler")
    assert is_workspace_run_followup("Kannst du den fehlgeschlagenen Test untersuchen?")
    assert is_workspace_run_followup("Der Grund dafür?")
    assert is_workspace_run_followup("Woran lag das?")


def test_plan_rejects_path_escape_and_forward_dependencies():
    with pytest.raises(ValueError):
        WorkspacePlan.model_validate({"goal": "x", "steps": [{"id": "x", "kind": "write", "path": "../x", "content": "x"}]})
    with pytest.raises(ValueError):
        WorkspacePlan.model_validate({
            "goal": "x",
            "steps": [{"id": "x", "kind": "touch", "path": "x", "depends_on": ["later"]}],
        })


@pytest.mark.asyncio
async def test_planner_uses_request_max_tokens_when_no_env_override(monkeypatch):
    monkeypatch.delenv("LIARA_AGENT_PLANNER_MAX_TOKENS", raising=False)
    inference = FakeInference(_plan())
    agent = WorkspaceAgent(
        inference_invoker=inference,
        tool_coordinator=FakeTools(),
        memory_service=FakeMemory(),
    )

    plan = await agent.create_plan("build translator", max_tokens=32768)

    assert inference.requests[0].max_tokens == 32768
    assert plan.planning["max_tokens"] == 32768


@pytest.mark.asyncio
async def test_repair_run_observes_real_inventory_before_planning():
    inference = FakeInference(_plan())
    tools = InventoryTools()
    agent = WorkspaceAgent(
        inference_invoker=inference,
        tool_coordinator=tools,
        memory_service=FakeMemory(),
    )

    result = await agent.run(
        "Repariere den Translator im WSL-Workspace",
        request_id="req",
        run_id="run",
        session_id="session",
    )

    assert result.status == "completed"
    assert tools.requests[0].parameters["context"] == "agent_workspace_preflight_inventory"
    assert "translation_worker/models.py" in inference.requests[0].prompt
    assert "tests/test_worker.py" in inference.requests[0].prompt
    assert ".venv/bin/pytest" not in inference.requests[0].prompt
    assert result.plan.planning["workspace_inventory_count"] == 2
    assert result.plan.planning["workspace_snapshot_files"] == 2
    assert "class TranslationOutput: pass" in inference.requests[0].prompt
    preflight_reads = [
        request for request in tools.requests
        if request.parameters.get("context") == "agent_workspace_preflight_read"
    ]
    assert len(preflight_reads) == 2


def test_failed_workspace_run_cannot_be_reported_as_success():
    grounded = Orchestrator._ground_workspace_agent_response(
        "Alles wurde erfolgreich angelegt.",
        {
            "workspace_agent": {
                "status": "step_failed",
                "steps": [{"step_id": "write", "status": "success", "error": None},
                          {"step_id": "run", "status": "failed", "error": "blocked"}],
                "validator": {},
            }
        },
    )
    assert "nicht als erfolgreich abgeschlossen" in grounded
    assert "run (failed): blocked" in grounded
    assert "erfolgreich angelegt" not in grounded


@pytest.mark.asyncio
async def test_workspace_run_artifact_roundtrip_keeps_validator_findings():
    memory = FakeMemory()
    agent = WorkspaceAgent(inference_invoker=FakeInference(_plan()), tool_coordinator=FakeTools(), memory_service=memory)
    result = WorkspaceRunResult(
        status="validation_failed",
        goal="translator",
        steps=[
            WorkspaceStepResult(step_id="write", kind="write", status="success", verified=True),
            WorkspaceStepResult(
                step_id="run_tests",
                kind="run",
                status="failed",
                verified=False,
                error="Command exited with code 1",
                output="FAILED tests/test_worker.py::test_empty_input - expected stable error",
            ),
        ],
        validator={
            "job_id": "job-x",
            "state": "failed",
            "passed": False,
            "findings": [{"severity": "error", "message": "syntax error", "file_path": "main.py", "line": 7}],
            "summary": {"exit_code": 1},
        },
    )

    persisted = await agent.persist_run_artifact(result, session_id="session-x", run_id="run-x")
    loaded = await agent.load_latest_run_artifact(session_id="session-x")

    assert persisted["status"] == "success"
    assert loaded["run_id"] == "run-x"
    assert loaded["validator"]["findings"][0]["message"] == "syntax error"
    assert "test_empty_input" in loaded["steps"][1]["output_excerpt"]
    grounded = Orchestrator._ground_workspace_agent_response(
        "Ich weiß es nicht.", {"workspace_run_history": {"artifact": loaded}}
    )
    assert "main.py:7: syntax error" in grounded
    assert "expected stable error" in grounded
    assert "Ich weiß es nicht" not in grounded


def test_step_budget_is_derived_from_cost_utility_math(monkeypatch):
    monkeypatch.setenv("LIARA_AGENT_COST_GAMMA", "2.0")
    plan = WorkspacePlan.model_validate({
        "goal": "large generated plan",
        "steps": [
            {"id": f"s{index}", "kind": "touch", "path": f"f{index}.txt"}
            for index in range(1, 65)
        ],
    })
    budget = WorkspaceMathBudget()
    decisions = [budget.evaluate(plan, index) for index in range(len(plan.steps))]

    assert decisions[0]["release"] is True
    assert decisions[-1]["release"] is False
    assert decisions[-1]["reason"] == "cost_exceeds_hard_max"
    assert decisions[-1]["cost_total"] > decisions[-1]["thresholds"]["hard_max"]
    assert decisions[0]["formula"].startswith("C(a)=")


@pytest.mark.asyncio
async def test_planner_falls_back_from_unavailable_preferred_provider():
    inference = FailoverInference(_plan())
    agent = WorkspaceAgent(inference_invoker=inference, tool_coordinator=FakeTools(), memory_service=FakeMemory())

    plan = await agent.create_plan("build translator", provider="openvino")

    assert inference.providers[0] == "openvino"
    assert inference.providers[1] != "openvino"
    assert plan.planning["attempts"][0]["error"] == "OPENVINO model directory not configured"
    assert plan.planning["attempts"][1]["status"] == "success"


@pytest.mark.asyncio
async def test_run_executes_sequential_sys_steps_and_validator():
    tools = FakeTools()
    agent = WorkspaceAgent(inference_invoker=FakeInference(_plan()), tool_coordinator=tools, memory_service=FakeMemory())
    result = await agent.run("build it", request_id="req", run_id="run", session_id="session")

    assert result.status == "completed"
    assert [request.parameters["command"] for request in tools.requests] == ["mkdir", "tee", "python"]
    assert all(step.released_next_step for step in result.steps)
    assert result.validator["passed"] is True
    assert tools.requests[1].parameters["stdin_text"] == "print('ok')\n"
    assert tools.requests[1].parameters["target_path"] == "/home/liara/workspace/demo/main.py"


@pytest.mark.asyncio
async def test_unverified_mutation_stops_before_next_step_and_validator():
    tools = FakeTools(verify=False)
    agent = WorkspaceAgent(inference_invoker=FakeInference(_plan()), tool_coordinator=tools, memory_service=FakeMemory())
    result = await agent.run("build it", request_id="req", run_id="run", session_id="session")

    assert result.status == "step_failed"
    assert len(tools.requests) == 1
    assert result.steps[0].released_next_step is False
    assert result.validator == {}


@pytest.mark.asyncio
async def test_transient_mutation_timeout_is_retried_once_and_recorded():
    tools = FakeTools(transient_failures=1)
    agent = WorkspaceAgent(
        inference_invoker=FakeInference(_plan()),
        tool_coordinator=tools,
        memory_service=FakeMemory(),
    )

    result = await agent.run("build it", request_id="req", run_id="run", session_id="session")

    assert result.status == "completed"
    assert result.steps[0].attempts == 2
    assert result.steps[0].evidence["execution_attempts"] == 2
    assert result.steps[0].evidence["recovered_after_transient_timeout"] is True


@pytest.mark.asyncio
async def test_governance_block_creates_idempotent_pending_handoff(monkeypatch, tmp_path):
    store_path = tmp_path / "sys_governance_workspace.json"
    events_path = tmp_path / "sys_governance_workspace.jsonl"
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(store_path))
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(events_path))
    tools = GovernanceBlockedTools()
    agent = WorkspaceAgent(
        inference_invoker=FakeInference(_plan()),
        tool_coordinator=tools,
        memory_service=FakeMemory(),
    )

    result = await agent.run("build it", request_id="req-gov", run_id="run-gov", session_id="session-gov")

    assert result.status == "awaiting_decision"
    assert len(result.steps) == 1
    step_result = result.steps[0]
    assert step_result.status == "awaiting_decision"
    proposal_id = step_result.evidence["proposal_id"]
    proposals = json.loads(store_path.read_text(encoding="utf-8"))
    proposal = proposals[proposal_id]
    assert proposal["decision"] == "pending"
    assert proposal["command"] == "mkdir"
    assert proposal["parameters"] == tools.requests[0].parameters
    assert proposal["traceability"]["run_id"] == "run-gov"
    assert proposal["handoff"]["step_id"] == "mkdir"
    assert proposal["handoff"]["state"] == "awaiting_decision"
    assert proposal["handoff"]["checkpoint"]["schema_version"] == 1
    assert proposal["handoff"]["checkpoint"]["step_index"] == 0
    assert proposal["handoff"]["checkpoint"]["plan"]["goal"] == "create a tiny worker"
    assert len(proposal["invocation_digest"]) == 64

    repeated = await agent._execute_step(
        result.plan.steps[0],
        {"request_id": "req-gov", "run_id": "run-gov", "session_id": "session-gov"},
    )
    assert repeated.evidence["proposal_id"] == proposal_id
    assert len(json.loads(store_path.read_text(encoding="utf-8"))) == 1
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert [event["event_type"] for event in events] == ["proposal_created"]


@pytest.mark.asyncio
async def test_approved_governance_checkpoint_resumes_without_replaying_blocked_step(monkeypatch, tmp_path):
    store_path = tmp_path / "sys_governance_resume.json"
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(store_path))
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(tmp_path / "sys_governance_resume.jsonl"))
    agent = WorkspaceAgent(
        inference_invoker=FakeInference(_plan()),
        tool_coordinator=GovernanceBlockedTools(),
        memory_service=FakeMemory(),
    )
    waiting = await agent.run(
        "build it",
        request_id="req-resume",
        run_id="run-resume",
        session_id="session-resume",
    )
    proposal_id = waiting.steps[0].evidence["proposal_id"]
    proposal = json.loads(store_path.read_text(encoding="utf-8"))[proposal_id]
    proposal["decision"] = "approved"
    continuation_tools = FakeTools()
    agent.tool_coordinator = continuation_tools
    approved_execution = ToolExecutionResult(
        tool_name="sys",
        status="success",
        output="ok",
        metadata={
            "mutation_verified": True,
            "mutation_evidence": {"target_path": "/home/liara/workspace/demo"},
        },
    )

    resumed = await agent.resume_from_governance_proposal(proposal, approved_execution)

    assert resumed.status == "completed"
    assert [step.step_id for step in resumed.steps] == ["mkdir", "write", "run"]
    assert resumed.steps[0].evidence["resumed_from_checkpoint"] is True
    assert [request.parameters["command"] for request in continuation_tools.requests] == ["tee", "python"]
    assert resumed.validator["passed"] is True


@pytest.mark.asyncio
async def test_governance_resume_rejects_rejected_or_tampered_checkpoint(monkeypatch, tmp_path):
    store_path = tmp_path / "sys_governance_reject.json"
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(store_path))
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(tmp_path / "sys_governance_reject.jsonl"))
    agent = WorkspaceAgent(
        inference_invoker=FakeInference(_plan()),
        tool_coordinator=GovernanceBlockedTools(),
        memory_service=FakeMemory(),
    )
    waiting = await agent.run("build it", request_id="req-reject", run_id="run-reject", session_id="session-reject")
    proposal = json.loads(store_path.read_text(encoding="utf-8"))[waiting.steps[0].evidence["proposal_id"]]
    approved_execution = ToolExecutionResult(
        tool_name="sys",
        status="success",
        output="ok",
        metadata={"mutation_verified": True},
    )

    proposal["decision"] = "rejected"
    with pytest.raises(ValueError, match="not approved"):
        await agent.resume_from_governance_proposal(proposal, approved_execution)

    proposal["decision"] = "approved"
    proposal["invocation_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        await agent.resume_from_governance_proposal(proposal, approved_execution)


@pytest.mark.asyncio
async def test_dependency_gate_is_injected_verified_and_tests_use_workspace_venv(monkeypatch):
    monkeypatch.setenv("LIARA_AGENT_DEPENDENCY_ALLOWLIST", "pydantic,pytest")
    payload = {
        "goal": "build tested worker",
        "steps": [
            {"id": "mkdir_pkg", "kind": "mkdir", "path": "demo"},
            {
                "id": "write_project",
                "kind": "write",
                "path": "pyproject.toml",
                "content": "[project]\nname='demo'\nversion='0.1.0'\ndependencies=['pydantic>=2.0']\n",
                "depends_on": ["mkdir_pkg"],
            },
            {
                "id": "write_test",
                "kind": "write",
                "path": "tests/test_demo.py",
                "content": "import pytest\nfrom pydantic import BaseModel\n\ndef test_ok(): assert True\n",
                "depends_on": ["write_project"],
            },
            {
                "id": "tests",
                "kind": "run",
                "command": "python3",
                "args": ["-m", "pytest", "-q"],
                "depends_on": ["write_test"],
            },
        ],
    }
    inference = FakeInference(payload)
    tools = FakeTools()
    agent = WorkspaceAgent(inference_invoker=inference, tool_coordinator=tools, memory_service=FakeMemory())

    result = await agent.run("build it", request_id="req", run_id="run", session_id="session")

    assert result.status == "completed"
    install_step = next(step for step in result.plan.steps if step.kind.value == "install")
    assert install_step.packages == ["pydantic>=2.0", "pytest"]
    commands = [request.parameters["command"] for request in tools.requests]
    assert commands[-3:] == ["venv-pip", "venv-pip", "python"]
    assert tools.requests[-1].parameters["args"] == ["-m", "pytest", "-q", "tests"]
    install_result = next(step for step in result.steps if step.kind.value == "install")
    assert install_result.verified is True
    assert install_result.evidence["verification"] == "venv_pip_show"


def test_workspace_agent_normalizes_planner_pytest_command():
    step = WorkspaceStep(
        id="tests",
        kind=WorkspaceStepKind.RUN,
        command="python3",
        args=["-m", "pytest", "tests/test_worker.py"],
    )
    assert WorkspaceAgent._normalize_run_args(step) == [
        "-m",
        "pytest",
        "-q",
        "tests/test_worker.py",
    ]


@pytest.mark.asyncio
async def test_unapproved_declared_dependency_blocks_planning(monkeypatch):
    monkeypatch.setenv("LIARA_AGENT_DEPENDENCY_ALLOWLIST", "pydantic,pytest")
    payload = {
        "goal": "unsafe dependency",
        "steps": [
            {
                "id": "project",
                "kind": "write",
                "path": "pyproject.toml",
                "content": "[project]\nname='demo'\nversion='0.1.0'\ndependencies=['unknown-package']\n",
            }
        ],
    }
    agent = WorkspaceAgent(
        inference_invoker=FakeInference(payload),
        tool_coordinator=FakeTools(),
        memory_service=FakeMemory(),
    )

    result = await agent.run("build it", request_id="req", run_id="run", session_id="session")

    assert result.status == "planning_failed"
    assert "dependency approval required" in (result.error or "")
