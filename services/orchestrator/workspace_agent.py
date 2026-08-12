"""Bounded plan/act/observe loop for LIARA's native WSL workspace.

This module deliberately does not expose a shell.  A model may propose only
typed operations; every operation is translated into the existing ``sys``
tool contract and remains subject to its command and path policies.
"""

from __future__ import annotations

import asyncio
import ast
import json
import os
import re
import sys
import time
import tomllib
from enum import Enum
from math import log2
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from services.contracts import (
    InferenceRequest,
    ContextScope,
    ContextSearchRequest,
    ContextUpsertRequest,
    ToolExecutionRequest,
    ToolExecutionResult,
    ValidatorResultRequest,
    ValidatorStatusRequest,
    ValidatorSubmitRequest,
)
from services.config import Settings
from services.tools.governance import create_pending_sys_governance_proposal, sys_governance_invocation_digest


_DEFAULT_ROOT = "/home/liara/workspace"
_IMPLEMENTATION_WORDS = re.compile(
    r"\b(?:anleg\w*|leg(?:e|en)|umsetz\w*|implementier\w*|erstell\w*|schreib\w*|bau\w*|"
    r"programmier\w*|hinzuf(?:ue|ü)g\w*|fix\w*|reparier\w*|beheb\w*|"
    r"aender\w*|änder\w*|aktualisier\w*|anpass\w*|"
    r"scaffold\w*|creat\w*|implement\w*|build\w*|writ\w*)\b",
    re.IGNORECASE,
)
_WORKSPACE_WORDS = re.compile(
    r"\b(workspace|projekt|projektcode|project|code|quellcode|codebasis|testcode|datei(?:en)?|ordner|modul|worker|service|"
    r"script|skript|package|paket)\b",
    re.IGNORECASE,
)
_REPAIR_WORDS = re.compile(
    r"\b(?:reparier\w*|beheb\w*|fix\w*|aender\w*|änder\w*|aktualisier\w*|anpass\w*)\b",
    re.IGNORECASE,
)
_WORKSPACE_FOLLOWUP = re.compile(
    r"\b(was fehlte|was ist fehlgeschlagen|warum.*fehl|welche.*fehler|"
    r"der grund daf(?:ue|ü)r|woran lag (?:das|es)|warum (?:war|ist) das|"
    r"(?:analysier|untersuch|pruef|prüf)\w*.*(?:fehler|fehlgeschlagen)|"
    r"(?:fehler|fehlgeschlagen).*(?:analys|untersuch|pruef|prüf)\w*|"
    r"validator.*(?:detail|finding|fehler|ergebnis)|findings?|what failed|why.*fail)\b",
    re.IGNORECASE,
)


def is_complex_workspace_request(query: str) -> bool:
    """Return true only for implementation requests aimed at the workspace."""
    text = str(query or "").strip()
    if not text or text.startswith("/sys "):
        return False
    return bool(_IMPLEMENTATION_WORDS.search(text) and _WORKSPACE_WORDS.search(text))


def is_workspace_run_followup(query: str) -> bool:
    return bool(_WORKSPACE_FOLLOWUP.search(str(query or "").strip()))


class WorkspaceStepKind(str, Enum):
    LIST = "list"
    READ = "read"
    MKDIR = "mkdir"
    WRITE = "write"
    TOUCH = "touch"
    INSTALL = "install"
    RUN = "run"


class WorkspaceStep(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,31}$")
    kind: WorkspaceStepKind
    path: str | None = None
    content: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    packages: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    reason: str = ""

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        path = PurePosixPath(value.strip())
        if not value.strip() or path.is_absolute() or ".." in path.parts:
            raise ValueError("workspace paths must be non-empty, relative, and confined")
        return str(path)

    @model_validator(mode="after")
    def validate_shape(self) -> "WorkspaceStep":
        path_kinds = {
            WorkspaceStepKind.LIST,
            WorkspaceStepKind.READ,
            WorkspaceStepKind.MKDIR,
            WorkspaceStepKind.WRITE,
            WorkspaceStepKind.TOUCH,
        }
        if self.kind in path_kinds and not self.path:
            raise ValueError(f"{self.kind.value} requires path")
        if self.kind == WorkspaceStepKind.WRITE and self.content is None:
            raise ValueError("write requires content")
        if self.kind == WorkspaceStepKind.RUN:
            if self.command not in {"python3", "julia"}:
                raise ValueError("run command must be python3 or julia")
            if not self.args:
                raise ValueError("run requires explicit args")
        if self.kind == WorkspaceStepKind.INSTALL:
            if not self.packages or len(self.packages) > 8:
                raise ValueError("install requires between 1 and 8 dependency specs")
            if self.path or self.content is not None or self.command or self.args:
                raise ValueError("install accepts packages only")
        return self


class WorkspacePlan(BaseModel):
    goal: str
    # 64 is an input-safety ceiling, not the runtime step budget.  The actual
    # release decision is derived from WorkspaceMathBudget below.
    steps: list[WorkspaceStep] = Field(min_length=1, max_length=64)
    planning: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_graph_and_budget(self) -> "WorkspacePlan":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("step ids must be unique")
        seen: set[str] = set()
        total_write_bytes = 0
        for step in self.steps:
            if any(dep not in seen for dep in step.depends_on):
                raise ValueError(f"step {step.id} has a missing or forward dependency")
            seen.add(step.id)
            total_write_bytes += len((step.content or "").encode("utf-8"))
        # Parser/transport safety only. Resource admission is mathematical.
        if total_write_bytes > 1024 * 1024:
            raise ValueError("plan exceeds the emergency transport ceiling")
        return self


class WorkspaceMathBudget:
    """Per-step C(a)/U(a) gate backed by LIARA's calibrated thresholds."""

    def __init__(self) -> None:
        profile = dict(Settings.reasoning_threshold_profile() or {})
        reasoning_span = max(1, int(Settings.MAX_REASONING_STEPS))
        self.soft_max = float(profile.get("soft_risk_max", 5.0)) * reasoning_span
        self.hard_max = float(profile.get("hard_risk_max", 8.0)) * reasoning_span
        self.alpha = float(os.getenv("LIARA_AGENT_COST_ALPHA", "0.35"))
        self.beta = float(os.getenv("LIARA_AGENT_COST_BETA", "0.00025"))
        self.gamma = float(os.getenv("LIARA_AGENT_COST_GAMMA", "0.75"))
        self.delta = float(os.getenv("LIARA_AGENT_COST_DELTA", "1.5"))
        self.profile = {
            "version": profile.get("version"),
            "source": profile.get("source"),
            "soft_max": round(self.soft_max, 6),
            "hard_max": round(self.hard_max, 6),
            "coefficients": {
                "alpha_depth": self.alpha,
                "beta_tokens": self.beta,
                "gamma_tools": self.gamma,
                "delta_entropy": self.delta,
            },
        }

    @staticmethod
    def _entropy(plan: WorkspacePlan) -> float:
        counts: dict[str, int] = {}
        for step in plan.steps:
            counts[step.kind.value] = counts.get(step.kind.value, 0) + 1
        total = max(1, len(plan.steps))
        probabilities = [count / total for count in counts.values()]
        raw = -sum(p * log2(p) for p in probabilities if p > 0)
        maximum = log2(len(probabilities)) if len(probabilities) > 1 else 1.0
        return min(1.0, max(0.0, raw / maximum))

    @staticmethod
    def _weight(step: WorkspaceStep) -> float:
        return {
            WorkspaceStepKind.LIST: 0.5,
            WorkspaceStepKind.READ: 0.5,
            WorkspaceStepKind.MKDIR: 0.75,
            WorkspaceStepKind.TOUCH: 0.75,
            WorkspaceStepKind.INSTALL: 1.75,
            WorkspaceStepKind.WRITE: 1.5,
            WorkspaceStepKind.RUN: 1.25,
        }[step.kind]

    def evaluate(self, plan: WorkspacePlan, step_index: int) -> dict[str, Any]:
        completed_slice = plan.steps[: step_index + 1]
        depth = step_index + 1
        tokens = sum(max(1, len((step.content or step.reason or "").encode("utf-8")) // 4) for step in completed_slice)
        tools = depth
        entropy = self._entropy(plan)
        depth_cost = self.alpha * depth
        token_cost = self.beta * tokens
        tool_cost = self.gamma * tools
        entropy_cost = self.delta * entropy
        cost_total = depth_cost + token_cost + tool_cost + entropy_cost
        total_weight = sum(self._weight(step) for step in plan.steps)
        progress_weight = sum(self._weight(step) for step in completed_slice)
        goal_progress = self.hard_max * (progress_weight / max(total_weight, 1e-9))
        utility = goal_progress - cost_total
        confidence_adjusted_utility = utility * (1.0 - entropy)
        fanout: dict[str, int] = {step.id: 0 for step in plan.steps}
        for candidate in plan.steps:
            for dependency in candidate.depends_on:
                fanout[dependency] = fanout.get(dependency, 0) + 1
        branching_factor = max(1.0, sum(fanout.values()) / max(1, len(fanout)))
        rds_v2 = log2(1 + (depth * branching_factor)) + (0.8 * entropy)
        policy_risk = max(
            ({
                WorkspaceStepKind.LIST: 0.02,
                WorkspaceStepKind.READ: 0.02,
                WorkspaceStepKind.MKDIR: 0.08,
                WorkspaceStepKind.TOUCH: 0.08,
                WorkspaceStepKind.INSTALL: 0.18,
                WorkspaceStepKind.WRITE: 0.12,
                WorkspaceStepKind.RUN: 0.20,
            }[step.kind] for step in completed_slice),
            default=0.0,
        )
        uncertainty_risk = entropy
        complexity_risk = rds_v2
        risk_total = (0.5 * policy_risk) + (0.2 * uncertainty_risk) + (0.3 * complexity_risk)
        actionable_risk = (0.5 * policy_risk) + (0.2 * uncertainty_risk)
        risk_soft = float(Settings.reasoning_threshold_profile().get("soft_risk_max", 5.0))
        risk_hard = float(Settings.reasoning_threshold_profile().get("hard_risk_max", 8.0))

        if actionable_risk > risk_hard:
            mode, release, reason = "hard", False, "actionable_risk_exceeds_hard_max"
        elif cost_total > self.hard_max:
            mode, release, reason = "hard", False, "cost_exceeds_hard_max"
        elif actionable_risk > risk_soft or cost_total > self.soft_max:
            mode = "soft"
            release = confidence_adjusted_utility > 0.0
            reason = "positive_utility_under_soft_control" if release else "negative_utility_under_soft_control"
        else:
            mode, release, reason = "advisory", True, "within_calibrated_budget"

        return {
            "formula": "C(a)=alpha*depth+beta*tokens+gamma*tools+delta*entropy; U(a)=goal_progress-C(a)",
            "depth": depth,
            "tokens": tokens,
            "tools": tools,
            "entropy": round(entropy, 6),
            "cost_components": {
                "depth": round(depth_cost, 6),
                "tokens": round(token_cost, 6),
                "tools": round(tool_cost, 6),
                "entropy": round(entropy_cost, 6),
            },
            "cost_total": round(cost_total, 6),
            "goal_progress": round(goal_progress, 6),
            "utility": round(utility, 6),
            "confidence_adjusted_utility": round(confidence_adjusted_utility, 6),
            "rds_v2": round(rds_v2, 6),
            "branching_factor": round(branching_factor, 6),
            "risk_total": round(risk_total, 6),
            "actionable_risk": round(actionable_risk, 6),
            "policy_risk": round(policy_risk, 6),
            "control_mode": mode,
            "release": release,
            "reason": reason,
            "thresholds": dict(self.profile),
            "compute_backend": "python",
            "compute_path": "fallback",
        }

    async def evaluate_async(self, plan: WorkspacePlan, step_index: int) -> dict[str, Any]:
        """Use LIARA's local Julia bridge first and preserve Python fallback."""
        python_decision = self.evaluate(plan, step_index)
        payload = {
            "depth": python_decision["depth"],
            "tokens": python_decision["tokens"],
            "tools": python_decision["tools"],
            "entropy": python_decision["entropy"],
            "branching_factor": python_decision["branching_factor"],
            "goal_progress": python_decision["goal_progress"],
            "policy_risk": python_decision["policy_risk"],
            "alpha": self.alpha,
            "beta": self.beta,
            "gamma": self.gamma,
            "delta": self.delta,
            "cost_soft": self.soft_max,
            "cost_hard": self.hard_max,
            "risk_soft": float(Settings.reasoning_threshold_profile().get("soft_risk_max", 5.0)),
            "risk_hard": float(Settings.reasoning_threshold_profile().get("hard_risk_max", 8.0)),
        }
        from services.simulation.bridge import JuliaBridge, JuliaBridgeError
        try:
            bridge = JuliaBridge(allowlist=list(dict.fromkeys([*Settings.julia_allowlist(), "workspace_budget"])))
            raw = await bridge.run("workspace_budget", payload)
            decision = dict(raw.get("decision") or {})
            required = {"cost_total", "utility", "control_mode", "release", "reason"}
            if not required.issubset(decision):
                raise JuliaBridgeError("workspace_budget returned an incomplete decision")
            decision.update({
                "formula": python_decision["formula"],
                "depth": python_decision["depth"],
                "tokens": python_decision["tokens"],
                "tools": python_decision["tools"],
                "entropy": python_decision["entropy"],
                "branching_factor": python_decision["branching_factor"],
                "thresholds": dict(self.profile),
                "compute_backend": "julia",
                "compute_path": "primary",
                "fallback_reason": None,
            })
            return decision
        except (JuliaBridgeError, ValueError, TypeError, KeyError) as exc:
            python_decision["fallback_reason"] = str(exc)
            return python_decision


class WorkspaceStepResult(BaseModel):
    step_id: str
    kind: WorkspaceStepKind
    status: str
    released_next_step: bool = False
    verified: bool = False
    output: Any = None
    error: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    math: dict[str, Any] = Field(default_factory=dict)
    attempts: int = 1


class WorkspaceRunResult(BaseModel):
    status: str
    goal: str
    plan: WorkspacePlan | None = None
    steps: list[WorkspaceStepResult] = Field(default_factory=list)
    validator: dict[str, Any] = Field(default_factory=dict)
    math_budget: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class WorkspaceAgent:
    """Generate and execute one bounded workspace plan, sequentially."""

    def __init__(self, *, inference_invoker: Any, tool_coordinator: Any, memory_service: Any):
        self.inference_invoker = inference_invoker
        self.tool_coordinator = tool_coordinator
        self.memory_service = memory_service
        self.workspace_root = os.getenv("LIARA_AGENT_WORKSPACE_ROOT", _DEFAULT_ROOT).rstrip("/")
        self.validator_timeout = float(os.getenv("LIARA_AGENT_VALIDATOR_TIMEOUT_SECONDS", "180"))
        self.sys_max_attempts = max(1, min(3, int(os.getenv("LIARA_AGENT_SYS_MAX_ATTEMPTS", "2"))))
        self.dependency_allowlist = frozenset(
            re.sub(r"[-_.]+", "-", item.strip()).lower()
            for item in os.getenv("LIARA_AGENT_DEPENDENCY_ALLOWLIST", "pydantic,pytest").split(",")
            if item.strip()
        )
        self.math_budget = WorkspaceMathBudget()

    async def _governance_handoff(
        self,
        *,
        step: WorkspaceStep,
        params: dict[str, Any],
        trace: dict[str, str],
        classification: dict[str, Any],
        checkpoint: dict[str, Any] | None = None,
    ) -> WorkspaceStepResult:
        command = str(params.get("command") or "")
        proposal = await asyncio.to_thread(
            create_pending_sys_governance_proposal,
            command=command,
            parameters=params,
            capability=f"workspace_{step.kind.value}",
            rationale=step.reason or f"Workspace step '{step.id}' requires governance approval",
            requested_by="workspace_agent",
            traceability={
                "request_id": trace.get("request_id"),
                "run_id": trace.get("run_id"),
                "session_id": trace.get("session_id"),
                "source": "workspace_agent",
                "context": params.get("context"),
            },
            handoff={
                "state": "awaiting_decision",
                "step_id": step.id,
                "step_kind": step.kind.value,
                "depends_on": list(step.depends_on),
                "workspace_root": self.workspace_root,
                "governance_classification": dict(classification),
                "checkpoint": dict(checkpoint or {}),
            },
        )
        return WorkspaceStepResult(
            step_id=step.id,
            kind=step.kind,
            status="awaiting_decision",
            released_next_step=False,
            verified=False,
            error="workspace step awaits governance decision",
            evidence={
                "governance_required": True,
                "proposal_id": proposal["proposal_id"],
                "invocation_digest": proposal["invocation_digest"],
                "decision": "pending",
                "classification": dict(classification),
            },
        )

    @staticmethod
    def _dependency_name(spec: str) -> str:
        match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", str(spec or ""))
        return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else ""

    def _augment_dependency_gate(self, plan: WorkspacePlan) -> WorkspacePlan:
        """Inject a deterministic, policy-gated venv sync before Python tests."""
        if any(step.kind == WorkspaceStepKind.INSTALL for step in plan.steps):
            return plan

        specs_by_name: dict[str, str] = {}
        workspace_packages = {
            PurePosixPath(step.path).parts[0]
            for step in plan.steps
            if step.kind == WorkspaceStepKind.WRITE and step.path and len(PurePosixPath(step.path).parts) > 1
        }
        has_tests = False
        for step in plan.steps:
            if step.kind != WorkspaceStepKind.WRITE or not step.path:
                continue
            if step.path == "pyproject.toml":
                try:
                    declared = tomllib.loads(step.content or "").get("project", {}).get("dependencies", [])
                except (tomllib.TOMLDecodeError, AttributeError, TypeError) as exc:
                    raise ValueError(f"dependency metadata is invalid: {exc}") from exc
                for spec in declared:
                    name = self._dependency_name(str(spec))
                    if name:
                        specs_by_name[name] = str(spec)
            if step.path.endswith(".py"):
                has_tests = has_tests or step.path.startswith("tests/") or PurePosixPath(step.path).name.startswith("test_")
                try:
                    tree = ast.parse(step.content or "", filename=step.path)
                except SyntaxError:
                    continue
                for node in ast.walk(tree):
                    roots: list[str] = []
                    if isinstance(node, ast.Import):
                        roots = [alias.name.split(".", 1)[0] for alias in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                        roots = [node.module.split(".", 1)[0]]
                    for root in roots:
                        normalized = self._dependency_name(root)
                        if root in sys.stdlib_module_names or root in workspace_packages:
                            continue
                        if normalized in self.dependency_allowlist and normalized not in specs_by_name:
                            specs_by_name[normalized] = root

        blocked = sorted(name for name in specs_by_name if name not in self.dependency_allowlist)
        if blocked:
            raise ValueError(f"dependency approval required: {', '.join(blocked)}")
        if has_tests and "pytest" not in specs_by_name:
            specs_by_name["pytest"] = "pytest"
        if not specs_by_name:
            return plan

        steps = [
            step.model_copy(update={"args": self._normalize_run_args(step)})
            if step.kind == WorkspaceStepKind.RUN
            else step
            for step in plan.steps
        ]
        first_run = next((index for index, step in enumerate(steps) if step.kind == WorkspaceStepKind.RUN), len(steps))
        previous = steps[first_run - 1].id if first_run > 0 else None
        install_id = "install_deps"
        suffix = 1
        known_ids = {step.id for step in steps}
        while install_id in known_ids:
            suffix += 1
            install_id = f"install_deps_{suffix}"
        install_step = WorkspaceStep(
            id=install_id,
            kind=WorkspaceStepKind.INSTALL,
            packages=list(specs_by_name.values()),
            depends_on=[previous] if previous else [],
            reason="Synchronize approved declared/test dependencies into the workspace .venv.",
        )
        steps.insert(first_run, install_step)
        if first_run + 1 < len(steps) and install_id not in steps[first_run + 1].depends_on:
            steps[first_run + 1].depends_on.append(install_id)
        if not any(step.kind == WorkspaceStepKind.RUN for step in steps) and has_tests:
            steps.append(
                WorkspaceStep(
                    id="run_tests" if "run_tests" not in known_ids else "run_workspace_tests",
                    kind=WorkspaceStepKind.RUN,
                    command="python3",
                    args=["-m", "pytest", "-q", "tests"],
                    depends_on=[install_id],
                    reason="Run workspace tests after dependency verification.",
                )
            )
        if len(steps) > 64:
            raise ValueError("dependency gate would exceed the plan step ceiling")
        return WorkspacePlan(goal=plan.goal, steps=steps, planning=dict(plan.planning))

    @staticmethod
    def _normalize_run_args(step: WorkspaceStep) -> list[str]:
        """Canonicalize the narrow generated-workspace pytest invocation."""
        args = list(step.args)
        if step.command != "python3" or args[:2] != ["-m", "pytest"]:
            return args
        tail = args[2:]
        if "-q" not in tail:
            tail.insert(0, "-q")
        if not any(not token.startswith("-") for token in tail):
            tail.append("tests")
        return ["-m", "pytest", *tail]

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any]:
        stripped = str(text or "").strip()
        fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, re.DOTALL | re.IGNORECASE)
        candidate = fenced.group(1) if fenced else stripped
        if not candidate.startswith("{"):
            start, end = candidate.find("{"), candidate.rfind("}")
            candidate = candidate[start : end + 1] if start >= 0 and end > start else candidate
        value = json.loads(candidate)
        if not isinstance(value, dict):
            raise ValueError("planner response must be a JSON object")
        return value

    async def create_plan(
        self,
        query: str,
        *,
        provider: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        workspace_inventory: list[str] | None = None,
        workspace_snapshot: dict[str, str] | None = None,
    ) -> WorkspacePlan:
        schema = json.dumps(WorkspacePlan.model_json_schema(), ensure_ascii=False)
        inventory = list(workspace_inventory or [])
        inventory_text = json.dumps(inventory, ensure_ascii=False)
        snapshot = dict(workspace_snapshot or {})
        snapshot_text = json.dumps(snapshot, ensure_ascii=False)
        prompt = f"""You are LIARA's workspace planner. Convert the request into a small executable plan.
Return JSON only, matching this schema: {schema}

Rules:
- Paths are relative to {_DEFAULT_ROOT}; never use absolute paths or '..'.
- Use mkdir before writing into a new directory.
- write.content contains the complete literal file content.
- run is optional and only supports python3 or julia with explicit args.
- No shell syntax, pipes, redirects, chmod, network, or deletion.
- Do not emit install steps; LIARA injects a deterministic dependency gate from pyproject.toml and test imports.
- Step and resource admission is decided at runtime by LIARA's mathematical cost/utility gate.
- Keep the plan minimal; parser safety ceilings are not a resource entitlement.
- Dependencies refer only to earlier step ids.
- Produce the smallest plan that fully implements the request.
- For repair/update requests, the workspace inventory below is authoritative.
- Never invent conventional directories such as src/ when they are absent from the inventory.
- Target exact existing paths from the inventory; LIST may use '.' or a directory proven by the inventory.
- The bounded source snapshot is the observed current state. Preserve existing public classes,
  imports, fields, and test contracts unless the request explicitly requires changing them.

Observed workspace inventory (relative paths, bounded):
{inventory_text}

Observed source snapshot (exact current contents, bounded):
{snapshot_text}

User request:
{query}
"""
        preferred = str(provider or "").strip()
        default_provider = str(getattr(Settings, "DEFAULT_LLM_PROVIDER", "ll_ol_fallback") or "ll_ol_fallback").strip()
        providers = list(dict.fromkeys(item for item in [preferred, default_provider, "ollama"] if item))
        attempts: list[dict[str, Any]] = []
        requested_max_tokens = int(max_tokens or 32768)
        planner_max_tokens = max(
            1024,
            int(os.getenv("LIARA_AGENT_PLANNER_MAX_TOKENS", str(requested_max_tokens))),
        )
        for attempt_index, candidate_provider in enumerate(providers, start=1):
            try:
                result = await self.inference_invoker.infer(
                    InferenceRequest(
                        prompt=prompt,
                        max_tokens=planner_max_tokens,
                        temperature=0.1,
                        provider=candidate_provider,
                        model=model if candidate_provider == preferred else None,
                        task_type="workspace_plan",
                        expected_fields=["goal", "steps"],
                    )
                )
                attempt = {
                    "attempt": attempt_index,
                    "requested_provider": candidate_provider,
                    "result_provider": result.provider,
                    "winner_provider": result.winner_provider,
                    "model": result.model,
                    "status": result.status,
                    "error": result.error,
                }
                attempts.append(attempt)
                if result.status != "success":
                    continue
                try:
                    plan = WorkspacePlan.model_validate(self._extract_json(result.content))
                    plan = self._augment_dependency_gate(plan)
                except (ValueError, TypeError, json.JSONDecodeError) as parse_error:
                    attempt["status"] = "invalid_plan"
                    attempt["error"] = str(parse_error)
                    continue
                plan.planning = {
                    "selected_provider": result.provider,
                    "winner_provider": result.winner_provider,
                    "model": result.model,
                    "max_tokens": planner_max_tokens,
                    "attempts": attempts,
                    "workspace_inventory_count": len(inventory),
                    "workspace_snapshot_files": len(snapshot),
                }
                return plan
            except Exception as inference_error:
                attempts.append(
                    {
                        "attempt": attempt_index,
                        "requested_provider": candidate_provider,
                        "status": "exception",
                        "error": str(inference_error),
                    }
                )
        errors = "; ".join(
            f"{item.get('requested_provider')}: {item.get('error') or item.get('status')}"
            for item in attempts
        )
        raise RuntimeError(f"workspace planning failed after {len(attempts)} provider attempt(s): {errors}")

    async def _collect_workspace_inventory(self, trace: dict[str, str]) -> tuple[list[str], str | None]:
        """Observe a bounded real file inventory before planning a repair."""
        params = {
            **trace,
            "source": "workspace_agent",
            "workdir": self.workspace_root,
            "command": "find",
            "args": [self.workspace_root, "-maxdepth", "4", "-type", "f"],
            "context": "agent_workspace_preflight_inventory",
        }
        raw = await self.tool_coordinator.execute_tool(
            ToolExecutionRequest(tool_name="sys", parameters=params, timeout_seconds=30)
        )
        if raw.status != "success":
            return [], raw.error or "workspace inventory failed"

        prefix = f"{self.workspace_root}/"
        ignored_parts = {".venv", ".pytest_cache", "__pycache__", ".liara_artifacts", ".git"}
        inventory: list[str] = []
        for line in str(raw.output or "").splitlines():
            absolute = line.strip()
            if not absolute.startswith(prefix):
                continue
            relative = absolute[len(prefix):]
            path = PurePosixPath(relative)
            if not relative or path.is_absolute() or ".." in path.parts:
                continue
            if any(part in ignored_parts or part.startswith(".") for part in path.parts):
                continue
            inventory.append(str(path))
            if len(inventory) >= 128 or sum(len(item) for item in inventory) >= 12000:
                break
        return sorted(dict.fromkeys(inventory)), None

    async def _collect_workspace_snapshot(
        self,
        inventory: list[str],
        trace: dict[str, str],
    ) -> tuple[dict[str, str], str | None]:
        """Read a math-bounded source snapshot through the audited SYS path."""
        configured_files = max(1, min(16, int(os.getenv("LIARA_AGENT_PREFLIGHT_MAX_FILES", "8"))))
        math_files = max(1, int(self.math_budget.hard_max / max(self.math_budget.gamma, 0.1)))
        max_files = min(configured_files, math_files)
        configured_chars = max(1024, min(65536, int(os.getenv("LIARA_AGENT_PREFLIGHT_MAX_CHARS", "32000"))))
        math_chars = max(1024, int(self.math_budget.hard_max / max(self.math_budget.beta, 1e-6)) * 4)
        max_chars = min(configured_chars, math_chars)

        candidates = [
            path
            for path in inventory
            if path.endswith(".py") or path == "pyproject.toml"
        ]
        candidates.sort(key=lambda path: (0 if path.startswith("tests/") else 1, path))
        snapshot: dict[str, str] = {}
        used_chars = 0
        for relative in candidates[:max_files]:
            absolute = self._absolute_path(relative)
            params = {
                **trace,
                "source": "workspace_agent",
                "workdir": self.workspace_root,
                "command": "cat",
                "args": [absolute],
                "context": "agent_workspace_preflight_read",
            }
            raw = await self.tool_coordinator.execute_tool(
                ToolExecutionRequest(tool_name="sys", parameters=params, timeout_seconds=30)
            )
            if raw.status != "success":
                return snapshot, raw.error or f"workspace preflight read failed: {relative}"
            content = str(raw.output or "")
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            snapshot[relative] = content[:remaining]
            used_chars += len(snapshot[relative])
        return snapshot, None

    def _absolute_path(self, relative: str) -> str:
        relative_path = PurePosixPath(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise ValueError("path escapes workspace")
        return str(PurePosixPath(self.workspace_root) / relative_path)

    def _parameters(self, step: WorkspaceStep, trace: dict[str, str]) -> dict[str, Any]:
        common = {**trace, "source": "workspace_agent", "workdir": self.workspace_root}
        if step.kind == WorkspaceStepKind.LIST:
            target = self._absolute_path(step.path or ".")
            return {**common, "command": "find", "args": [target, "-maxdepth", "2", "-type", "f"], "context": "agent_workspace_list"}
        if step.kind == WorkspaceStepKind.READ:
            target = self._absolute_path(step.path or "")
            return {**common, "command": "cat", "args": [target], "context": "agent_workspace_read"}
        if step.kind == WorkspaceStepKind.MKDIR:
            target = self._absolute_path(step.path or "")
            return {**common, "command": "mkdir", "args": ["-p", target], "target_path": target, "storage_scope": "wsl_workspace", "context": "agent_workspace_mkdir"}
        if step.kind == WorkspaceStepKind.TOUCH:
            target = self._absolute_path(step.path or "")
            return {**common, "command": "touch", "args": [target], "target_path": target, "storage_scope": "wsl_workspace", "context": "agent_workspace_touch"}
        if step.kind == WorkspaceStepKind.WRITE:
            target = self._absolute_path(step.path or "")
            return {**common, "command": "tee", "args": [target], "stdin_text": step.content or "", "target_path": target, "write_mode": "overwrite", "storage_scope": "wsl_workspace", "context": "agent_workspace_write"}
        if step.kind == WorkspaceStepKind.INSTALL:
            venv_path = self._absolute_path(".venv")
            return {
                **common,
                "command": "venv-pip",
                "args": ["install", "--disable-pip-version-check", "--no-input", *step.packages],
                "timeout": int(os.getenv("LIARA_AGENT_DEPENDENCY_TIMEOUT_SECONDS", "180")),
                "target_path": venv_path,
                "storage_scope": "wsl_workspace",
                "write_mode": "venv_install",
                "context": "agent_workspace_dependency_install",
            }
        command = "python" if step.command == "python3" else step.command
        return {
            **common,
            "command": command,
            "args": list(step.args),
            "timeout": int(os.getenv("LIARA_AGENT_TEST_TIMEOUT_SECONDS", "120")),
            "context": "agent_workspace_execute",
        }

    async def _result_from_execution(
        self,
        step: WorkspaceStep,
        raw: ToolExecutionResult,
        trace: dict[str, str],
        *,
        attempts: int = 1,
    ) -> WorkspaceStepResult:
        if step.kind == WorkspaceStepKind.INSTALL:
            if raw.status != "success":
                return WorkspaceStepResult(
                    step_id=step.id,
                    kind=step.kind,
                    status=raw.status,
                    error=raw.error,
                    verified=False,
                    evidence={"packages": list(step.packages), "phase": "install"},
                )
            params = self._parameters(step, trace)
            verify_params = {
                **{
                    key: value
                    for key, value in params.items()
                    if key not in {"target_path", "write_mode", "timeout"}
                },
                "args": ["show", *[self._dependency_name(spec) for spec in step.packages]],
                "context": "agent_workspace_dependency_verify",
                "timeout": 60,
            }
            verify_raw = await self.tool_coordinator.execute_tool(
                ToolExecutionRequest(tool_name="sys", parameters=verify_params, timeout_seconds=75)
            )
            verified = verify_raw.status == "success"
            return WorkspaceStepResult(
                step_id=step.id,
                kind=step.kind,
                status="success" if verified else "failed",
                released_next_step=verified,
                verified=verified,
                output=raw.output,
                error=None if verified else verify_raw.error or "dependency verification failed",
                evidence={
                    "packages": list(step.packages),
                    "verification": "venv_pip_show",
                    "verified": verified,
                },
                attempts=attempts,
            )

        mutation = step.kind in {WorkspaceStepKind.MKDIR, WorkspaceStepKind.WRITE, WorkspaceStepKind.TOUCH}
        verified = bool(raw.metadata.get("mutation_verified")) if mutation else raw.status == "success"
        released = raw.status == "success" and verified
        evidence = dict(raw.metadata.get("mutation_evidence") or {})
        if attempts > 1:
            evidence["execution_attempts"] = attempts
            evidence["recovered_after_transient_timeout"] = released
        return WorkspaceStepResult(
            step_id=step.id,
            kind=step.kind,
            status=raw.status,
            released_next_step=released,
            verified=verified,
            output=raw.output,
            error=raw.error,
            evidence=evidence,
            attempts=attempts,
        )

    async def _execute_step(
        self,
        step: WorkspaceStep,
        trace: dict[str, str],
        *,
        checkpoint: dict[str, Any] | None = None,
    ) -> WorkspaceStepResult:
        params = self._parameters(step, trace)
        if step.kind == WorkspaceStepKind.INSTALL:
            install_raw = await self.tool_coordinator.execute_tool(
                ToolExecutionRequest(tool_name="sys", parameters=params, timeout_seconds=210)
            )
            if install_raw.status != "success":
                if install_raw.metadata.get("governance_required"):
                    return await self._governance_handoff(
                        step=step,
                        params=params,
                        trace=trace,
                        classification=dict(install_raw.metadata.get("governance_classification") or {}),
                        checkpoint=checkpoint,
                    )
            return await self._result_from_execution(step, install_raw, trace)
        mutation = step.kind in {WorkspaceStepKind.MKDIR, WorkspaceStepKind.WRITE, WorkspaceStepKind.TOUCH}
        attempts = 0
        raw = None
        while attempts < self.sys_max_attempts:
            attempts += 1
            raw = await self.tool_coordinator.execute_tool(
                ToolExecutionRequest(tool_name="sys", parameters=params, timeout_seconds=60)
            )
            if raw.status == "success":
                break
            error_text = str(raw.error or "").lower()
            transient_timeout = bool(raw.metadata.get("transient_error")) or "timed out" in error_text
            if not mutation or not transient_timeout or attempts >= self.sys_max_attempts:
                break
            await asyncio.sleep(0.1)

        assert raw is not None
        if raw.metadata.get("governance_required"):
            return await self._governance_handoff(
                step=step,
                params=params,
                trace=trace,
                classification=dict(raw.metadata.get("governance_classification") or {}),
                checkpoint=checkpoint,
            )
        return await self._result_from_execution(step, raw, trace, attempts=attempts)

    def _validator_workspace(self) -> str:
        configured = os.getenv("LIARA_AGENT_VALIDATOR_WORKSPACE")
        if configured:
            return configured
        if os.name == "nt" and self.workspace_root.startswith("/home/"):
            distro = os.getenv("LIARA_WSL_DISTRO", "Debian")
            return rf"\\wsl.localhost\{distro}" + self.workspace_root.replace("/", "\\")
        return self.workspace_root

    @staticmethod
    def _artifact_payload(result: WorkspaceRunResult, *, session_id: str, run_id: str) -> dict[str, Any]:
        validator = dict(result.validator or {})
        findings = []
        for raw in list(validator.get("findings") or [])[:50]:
            item = dict(raw or {})
            item["message"] = str(item.get("message") or "")[:1000]
            findings.append(item)
        return {
            "artifact_type": "workspace_agent_run",
            "session_id": session_id,
            "run_id": run_id,
            "created_at": time.time(),
            "goal": result.goal,
            "status": result.status,
            "error": result.error,
            "steps": [
                {
                    "step_id": step.step_id,
                    "kind": step.kind.value,
                    "status": step.status,
                    "verified": step.verified,
                    "error": step.error,
                    "output_excerpt": (
                        str(step.output or "")[-6000:]
                        if step.status != "success" and step.kind == WorkspaceStepKind.RUN
                        else None
                    ),
                    "evidence": step.evidence,
                    "math": step.math,
                    "attempts": step.attempts,
                }
                for step in result.steps
            ],
            "validator": {
                "job_id": validator.get("job_id"),
                "state": validator.get("state"),
                "passed": validator.get("passed"),
                "findings": findings,
                "artifacts": list(validator.get("artifacts") or []),
                "summary": dict(validator.get("summary") or {}),
                "error": validator.get("error"),
            },
            "math_budget": dict(result.math_budget or {}),
        }

    @staticmethod
    def _redact_artifact_json(content: str) -> str:
        content = re.sub(
            r"(?i)(api[_-]?key|token|password|secret)(\\?\"?\s*[:=]\s*\\?\"?)[^\"\\\s,}]+",
            r"\1\2[REDACTED]",
            content,
        )
        return re.sub(r"(?i)(authorization\s*:\s*bearer\s+)[^\s\"\\]+", r"\1[REDACTED]", content)

    async def persist_run_artifact(self, result: WorkspaceRunResult, *, session_id: str, run_id: str) -> dict[str, Any]:
        payload = self._artifact_payload(result, session_id=session_id, run_id=run_id)
        content = self._redact_artifact_json(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        response = await self.memory_service.context_upsert(
            ContextUpsertRequest(
                document_id=f"workspace_agent_run:{session_id}:{run_id}",
                content=content,
                scope=ContextScope(session_id=session_id, run_id=run_id),
                memory_tier="short_term",
                promotion_state="none",
                metadata={
                    "source": "workspace_agent",
                    "artifact_type": "workspace_agent_run",
                    "validation_status": result.status,
                    "session_id": session_id,
                    "run_id": run_id,
                    "created_at": payload["created_at"],
                },
            )
        )
        return {
            "document_id": f"workspace_agent_run:{session_id}:{run_id}",
            "status": response.status.status,
            "backend": response.status.backend,
        }

    async def load_latest_run_artifact(self, *, session_id: str) -> dict[str, Any] | None:
        response = await self.memory_service.context_search(
            ContextSearchRequest(
                query="workspace agent validator fehlgeschlagen findings validation failed",
                scope=ContextScope(session_id=session_id),
                top_k=20,
            )
        )
        candidates: list[tuple[float, dict[str, Any]]] = []
        for document in response.items:
            metadata = dict(getattr(document, "metadata", {}) or {})
            if metadata.get("artifact_type") != "workspace_agent_run":
                continue
            try:
                payload = json.loads(document.content)
            except (TypeError, json.JSONDecodeError):
                continue
            candidates.append((float(payload.get("created_at") or metadata.get("created_at") or 0.0), payload))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    async def _validate(self, trace: dict[str, str]) -> dict[str, Any]:
        submit = await self.memory_service.validator_submit(
            ValidatorSubmitRequest(
                workspace=self._validator_workspace(),
                scope="quick",
                request_id=trace.get("request_id"),
                run_id=trace.get("run_id"),
                session_id=trace.get("session_id"),
                source="workspace_agent",
                context="workspace_agent_final_gate",
            )
        )
        deadline = asyncio.get_running_loop().time() + self.validator_timeout
        state = submit.state
        while state in {"queued", "running"} and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            status = await self.memory_service.validator_status(ValidatorStatusRequest(job_id=submit.job_id))
            state = status.state
        if state in {"queued", "running"}:
            return {"job_id": submit.job_id, "state": "failed", "error": "validator_timeout", "passed": False}
        result = await self.memory_service.validator_result(ValidatorResultRequest(job_id=submit.job_id))
        return {
            "job_id": result.job_id,
            "state": result.state,
            "passed": result.state == "completed" and not result.findings,
            "findings": [item.model_dump() for item in result.findings],
            "artifacts": list(result.artifacts),
            "summary": dict(result.summary),
        }

    @staticmethod
    def _resume_checkpoint(
        *,
        plan: WorkspacePlan,
        results: list[WorkspaceStepResult],
        completed: set[str],
        step_index: int,
        trace: dict[str, str],
        math_decision: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "plan": plan.model_dump(mode="json"),
            "results": [item.model_dump(mode="json") for item in results],
            "completed_step_ids": sorted(completed),
            "step_index": step_index,
            "trace": dict(trace),
            "math_decision": dict(math_decision),
        }

    async def resume_from_governance_proposal(
        self,
        proposal: dict[str, Any],
        approved_execution: ToolExecutionResult,
    ) -> WorkspaceRunResult:
        """Continue an exact checkpoint after the API consumed its approval once."""
        handoff = proposal.get("handoff") if isinstance(proposal.get("handoff"), dict) else {}
        checkpoint = handoff.get("checkpoint") if isinstance(handoff.get("checkpoint"), dict) else {}
        if int(checkpoint.get("schema_version") or 0) != 1:
            raise ValueError("workspace governance proposal has no supported resume checkpoint")
        if str(proposal.get("decision") or "") != "approved":
            raise ValueError("workspace governance proposal is not approved")

        plan = WorkspacePlan.model_validate(checkpoint.get("plan"))
        step_index = int(checkpoint.get("step_index"))
        if step_index < 0 or step_index >= len(plan.steps):
            raise ValueError("workspace governance checkpoint step index is invalid")
        step = plan.steps[step_index]
        if str(handoff.get("step_id") or "") != step.id:
            raise ValueError("workspace governance checkpoint step does not match handoff")

        trace = {key: str(value or "") for key, value in dict(checkpoint.get("trace") or {}).items()}
        expected_parameters = self._parameters(step, trace)
        expected_digest = sys_governance_invocation_digest(str(expected_parameters.get("command") or ""), expected_parameters)
        if expected_digest != str(proposal.get("invocation_digest") or ""):
            raise ValueError("workspace governance checkpoint action digest does not match proposal")

        results = [WorkspaceStepResult.model_validate(item) for item in list(checkpoint.get("results") or [])]
        completed = {str(item) for item in list(checkpoint.get("completed_step_ids") or [])}
        if completed != {item.step_id for item in results if item.released_next_step}:
            raise ValueError("workspace governance checkpoint completed-step set is inconsistent")

        outcome = await self._result_from_execution(step, approved_execution, trace)
        outcome.math = dict(checkpoint.get("math_decision") or {})
        outcome.evidence = {
            **dict(outcome.evidence or {}),
            "governance_proposal_id": proposal.get("proposal_id"),
            "governance_invocation_digest": proposal.get("invocation_digest"),
            "resumed_from_checkpoint": True,
        }
        results.append(outcome)
        if not outcome.released_next_step:
            return WorkspaceRunResult(
                status="step_failed",
                goal=plan.goal,
                plan=plan,
                steps=results,
                math_budget=self.math_budget.profile,
                error=outcome.error or "approved workspace step was not verified",
            )
        completed.add(step.id)

        for next_index in range(step_index + 1, len(plan.steps)):
            next_step = plan.steps[next_index]
            if any(dep not in completed for dep in next_step.depends_on):
                results.append(
                    WorkspaceStepResult(
                        step_id=next_step.id,
                        kind=next_step.kind,
                        status="blocked",
                        error="dependency_not_released",
                    )
                )
                return WorkspaceRunResult(
                    status="blocked",
                    goal=plan.goal,
                    plan=plan,
                    steps=results,
                    math_budget=self.math_budget.profile,
                )
            math_decision = await self.math_budget.evaluate_async(plan, next_index)
            if not math_decision["release"]:
                results.append(
                    WorkspaceStepResult(
                        step_id=next_step.id,
                        kind=next_step.kind,
                        status="budget_blocked",
                        error=math_decision["reason"],
                        math=math_decision,
                    )
                )
                return WorkspaceRunResult(
                    status="budget_blocked",
                    goal=plan.goal,
                    plan=plan,
                    steps=results,
                    math_budget=self.math_budget.profile,
                    error=math_decision["reason"],
                )
            next_checkpoint = self._resume_checkpoint(
                plan=plan,
                results=results,
                completed=completed,
                step_index=next_index,
                trace=trace,
                math_decision=math_decision,
            )
            next_outcome = await self._execute_step(next_step, trace, checkpoint=next_checkpoint)
            next_outcome.math = math_decision
            results.append(next_outcome)
            if not next_outcome.released_next_step:
                status = "awaiting_decision" if next_outcome.status == "awaiting_decision" else "step_failed"
                return WorkspaceRunResult(
                    status=status,
                    goal=plan.goal,
                    plan=plan,
                    steps=results,
                    math_budget=self.math_budget.profile,
                    error=next_outcome.error or "step was not verified",
                )
            completed.add(next_step.id)

        validator = await self._validate(trace)
        status = "completed" if validator.get("passed") else "validation_failed"
        return WorkspaceRunResult(
            status=status,
            goal=plan.goal,
            plan=plan,
            steps=results,
            validator=validator,
            math_budget=self.math_budget.profile,
        )

    async def run(
        self,
        query: str,
        *,
        request_id: str,
        run_id: str,
        session_id: str,
        provider: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> WorkspaceRunResult:
        trace = {"request_id": request_id, "run_id": run_id, "session_id": session_id}
        workspace_inventory: list[str] = []
        workspace_snapshot: dict[str, str] = {}
        if _REPAIR_WORDS.search(query):
            workspace_inventory, inventory_error = await self._collect_workspace_inventory(trace)
            if inventory_error:
                return WorkspaceRunResult(
                    status="observation_failed",
                    goal=query,
                    error=inventory_error,
                )
            workspace_snapshot, snapshot_error = await self._collect_workspace_snapshot(
                workspace_inventory,
                trace,
            )
            if snapshot_error:
                return WorkspaceRunResult(
                    status="observation_failed",
                    goal=query,
                    error=snapshot_error,
                )
        try:
            plan = await self.create_plan(
                query,
                provider=provider,
                model=model,
                max_tokens=max_tokens,
                workspace_inventory=workspace_inventory,
                workspace_snapshot=workspace_snapshot,
            )
        except Exception as exc:
            return WorkspaceRunResult(status="planning_failed", goal=query, error=str(exc))

        results: list[WorkspaceStepResult] = []
        completed: set[str] = set()
        for step_index, step in enumerate(plan.steps):
            if any(dep not in completed for dep in step.depends_on):
                results.append(WorkspaceStepResult(step_id=step.id, kind=step.kind, status="blocked", error="dependency_not_released"))
                return WorkspaceRunResult(status="blocked", goal=plan.goal, plan=plan, steps=results, math_budget=self.math_budget.profile)
            math_decision = await self.math_budget.evaluate_async(plan, step_index)
            if not math_decision["release"]:
                results.append(
                    WorkspaceStepResult(
                        step_id=step.id,
                        kind=step.kind,
                        status="budget_blocked",
                        error=math_decision["reason"],
                        math=math_decision,
                    )
                )
                return WorkspaceRunResult(
                    status="budget_blocked",
                    goal=plan.goal,
                    plan=plan,
                    steps=results,
                    math_budget=self.math_budget.profile,
                    error=math_decision["reason"],
                )
            checkpoint = self._resume_checkpoint(
                plan=plan,
                results=results,
                completed=completed,
                step_index=step_index,
                trace=trace,
                math_decision=math_decision,
            )
            outcome = await self._execute_step(step, trace, checkpoint=checkpoint)
            outcome.math = math_decision
            results.append(outcome)
            if not outcome.released_next_step:
                if outcome.status == "awaiting_decision":
                    return WorkspaceRunResult(
                        status="awaiting_decision",
                        goal=plan.goal,
                        plan=plan,
                        steps=results,
                        math_budget=self.math_budget.profile,
                        error=outcome.error,
                    )
                return WorkspaceRunResult(status="step_failed", goal=plan.goal, plan=plan, steps=results, math_budget=self.math_budget.profile, error=outcome.error or "step was not verified")
            completed.add(step.id)

        validator = await self._validate(trace)
        status = "completed" if validator.get("passed") else "validation_failed"
        return WorkspaceRunResult(status=status, goal=plan.goal, plan=plan, steps=results, validator=validator, math_budget=self.math_budget.profile)
