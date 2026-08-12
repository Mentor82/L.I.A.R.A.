"""Central SYS governance classification shared by every tool caller."""

from __future__ import annotations

import os
import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from services.tools.builtin.sys_command_policy import check_command_request

SysGovernanceMode = Literal["off", "risk_based", "all"]

_MUTATION_COMMANDS = frozenset({"tee", "mkdir", "touch", "cp", "mv", "venv-pip"})
_NETWORK_COMMANDS = frozenset({"curl", "wget"})
_CODE_EXECUTION_COMMANDS = frozenset({"python", "python3", "julia", "bash", "sh", "pwsh", "powershell"})
_STORE_LOCK = threading.Lock()
_RUNTIME_PARAMETER_KEYS = frozenset(
    {"proposal_id", "request_id", "run_id", "session_id", "source", "context", "_governance_authorized"}
)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def sys_governance_mode() -> SysGovernanceMode:
    configured = str(os.getenv("LIARA_SYS_GOVERNANCE_MODE") or "").strip().lower()
    if configured in {"off", "risk_based", "all"}:
        return configured  # type: ignore[return-value]
    if _truthy(os.getenv("LIARA_SYS_GOVERNANCE_ENFORCE")):
        return "all"
    return "off"


def classify_sys_governance(parameters: dict[str, Any]) -> dict[str, Any]:
    command = str(parameters.get("command") or "").strip()
    base_command = command.split(maxsplit=1)[0].lower() if command else ""
    args = parameters.get("args") if isinstance(parameters.get("args"), list) else []
    mutation = bool(
        base_command in _MUTATION_COMMANDS
        or parameters.get("write_mode") is not None
        or parameters.get("target_path") is not None
    )
    network = bool(
        base_command in _NETWORK_COMMANDS
        or (base_command == "venv-pip" and args and str(args[0]).lower() == "install")
    )
    code_execution = base_command in _CODE_EXECUTION_COMMANDS
    policy_result = check_command_request(base_command, [str(value) for value in args]) if base_command else None
    # A policy-validated curl request is LIARA's restricted read-only web
    # capability. Its W/G/B profile already rejects writes, uploads,
    # credentials, proxies, unsafe schemes and malformed greylist values.
    # Requiring a second approval merely because it uses the network would
    # override that capability policy and prevent normal web research.
    policy_validated_read = bool(
        base_command == "curl"
        and policy_result is not None
        and policy_result.allowed
        and not mutation
        and not code_execution
    )
    requires_governance = mutation or code_execution or (network and not policy_validated_read)
    reasons: list[str] = []
    if mutation:
        reasons.append("mutation")
    if network:
        reasons.append("network")
    if code_execution:
        reasons.append("code_execution")
    return {
        "command": base_command,
        "requires_governance": requires_governance,
        "risk_level": "high" if mutation or code_execution else "medium" if network else "low",
        "reasons": reasons,
        "policy_allowed": policy_result.allowed if policy_result is not None else None,
        "policy_error": policy_result.error if policy_result is not None else None,
        "policy_error_type": policy_result.error_type if policy_result is not None else None,
        "policy_class": policy_result.policy_class if policy_result is not None else None,
        "policy_validated_read": policy_validated_read,
    }


def sys_governance_block_reason(parameters: dict[str, Any]) -> str | None:
    mode = sys_governance_mode()
    if mode == "off":
        return None
    classification = classify_sys_governance(parameters)
    # Command-policy denials are final and must reach the SYS policy error
    # path; governance approval must never be offered as a way around them.
    if (
        classification.get("policy_allowed") is False
        and classification.get("reasons") == ["network"]
    ):
        return None
    authorized = bool(parameters.get("_governance_authorized")) and bool(parameters.get("proposal_id"))
    requires_authorization = mode == "all" or bool(classification["requires_governance"])
    if not requires_authorization or authorized:
        return None
    reasons = ",".join(classification["reasons"]) or "all_sys_calls"
    return f"SYS governance authorization required (mode={mode}, reasons={reasons})"


def sys_governance_store_path() -> Path:
    configured = str(os.getenv("LIARA_SYS_GOVERNANCE_STORE_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "logs" / "services" / "sys_governance_proposals.json"


def sys_governance_events_path() -> Path:
    configured = str(os.getenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH") or "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "logs" / "services" / "sys_governance_events.jsonl"


def load_sys_governance_proposals(path: Path | None = None) -> dict[str, dict[str, Any]]:
    target = path or sys_governance_store_path()
    if not target.exists():
        return {}
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(value.get("proposal_id") or key): value
        for key, value in payload.items()
        if isinstance(value, dict) and str(value.get("proposal_id") or key).strip()
    }


def persist_sys_governance_proposals(proposals: dict[str, dict[str, Any]], path: Path | None = None) -> None:
    target = path or sys_governance_store_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(proposals, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def append_sys_governance_event(event: dict[str, Any], path: Path | None = None) -> None:
    target = path or sys_governance_events_path()
    payload = dict(event)
    payload.setdefault("timestamp", datetime.now(UTC).isoformat())
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        handle.write("\n")


def sys_governance_invocation_digest(command: str, parameters: dict[str, Any]) -> str:
    governed = {
        str(key): value
        for key, value in dict(parameters or {}).items()
        if str(key) not in _RUNTIME_PARAMETER_KEYS and str(key) != "command"
    }
    payload = {"command": str(command or "").strip(), **governed}
    canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_pending_sys_governance_proposal(
    *,
    command: str,
    parameters: dict[str, Any],
    capability: str,
    rationale: str,
    requested_by: str,
    traceability: dict[str, Any],
    handoff: dict[str, Any] | None = None,
    max_invocations: int = 1,
    origin: str = "workspace_agent_handoff",
) -> dict[str, Any]:
    classification = classify_sys_governance(parameters)
    invocation_digest = sys_governance_invocation_digest(command, parameters)
    normalized_handoff = dict(handoff or {})
    handoff_key = str(normalized_handoff.get("handoff_key") or "").strip()
    if not handoff_key:
        handoff_key = ":".join(
            (
                str(traceability.get("run_id") or traceability.get("request_id") or "unknown"),
                str(normalized_handoff.get("step_id") or "unknown"),
                invocation_digest,
            )
        )
    normalized_handoff["handoff_key"] = handoff_key

    with _STORE_LOCK:
        proposals = load_sys_governance_proposals()
        for existing in proposals.values():
            existing_handoff = existing.get("handoff") if isinstance(existing.get("handoff"), dict) else {}
            if (
                str(existing.get("decision") or "") == "pending"
                and str(existing_handoff.get("handoff_key") or "") == handoff_key
            ):
                return existing

        proposal_id = f"sys-prop-{uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        proposal = {
            "proposal_id": proposal_id,
            "tool_name": "sys",
            "command": str(command),
            "parameters": {key: value for key, value in dict(parameters).items() if key != "_governance_authorized"},
            "invocation_digest": invocation_digest,
            "max_invocations": max(1, min(int(max_invocations), 10)),
            "invocation": {"state": "not_invoked", "attempt_count": 0, "success_count": 0},
            "capability": capability,
            "rationale": rationale,
            "requested_by": requested_by,
            "policy_check": {
                "allowed": True,
                "risk_level": classification["risk_level"],
                "reasons": list(classification["reasons"]),
            },
            "decision": "pending",
            "decision_reason": None,
            "decided_by": None,
            "created_at": now,
            "updated_at": now,
            "traceability": dict(traceability),
            "handoff": normalized_handoff,
        }
        proposals[proposal_id] = proposal
        persist_sys_governance_proposals(proposals)
        append_sys_governance_event(
            {
                "event_type": "proposal_created",
                "proposal_id": proposal_id,
                "tool_name": "sys",
                "command": command,
                "policy_allowed": True,
                "policy_risk_level": classification["risk_level"],
                "origin": str(origin or "workspace_agent_handoff"),
                "traceability": dict(traceability),
            }
        )
        return proposal


__all__ = [
    "SysGovernanceMode",
    "classify_sys_governance",
    "sys_governance_block_reason",
    "sys_governance_mode",
    "append_sys_governance_event",
    "create_pending_sys_governance_proposal",
    "load_sys_governance_proposals",
    "persist_sys_governance_proposals",
    "sys_governance_events_path",
    "sys_governance_invocation_digest",
    "sys_governance_store_path",
]
