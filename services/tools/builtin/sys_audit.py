"""Audit logging layer for /sys requests.

Records command, args, policy decision, execution outcome and sizes
of stdout/stderr for every /sys call. No raw output content is stored —
only byte-lengths for privacy and size control.

v0.0.1 — structured audit entries written to a rotating log file.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from uuid import uuid4

if TYPE_CHECKING:
    from services.tools.builtin.sys_audit_repository import PostgresSysAuditRepository

_DEFAULT_REQUEST_ID = "missing_request_id"
_DEFAULT_SOURCE = "unknown"

# ---------------------------------------------------------------------------
# Audit entry
# ---------------------------------------------------------------------------

@dataclass
class SysAuditEntry:
    command: str
    args: list[str] | None
    policy_decision: str          # "allowed" | "blocked"
    policy_reason: str | None     # None when allowed
    exit_code: int | None         # None when blocked (never executed)
    duration_ms: float | None     # None when blocked
    stdout_bytes: int | None      # None when blocked
    stderr_bytes: int | None      # None when blocked
    stdin_bytes: int | None = None
    stdin_sha256: str | None = None
    target_path: str | None = None
    storage_scope: str | None = None
    retention_hint_seconds: int | None = None
    write_mode: str | None = None
    judge_score: dict[str, Any] | None = None
    risk_flags: list[str] | None = None
    judge_constraints: dict[str, Any] | None = None
    request_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    source: str | None = None
    context: str | None = None
    proposal_id: str | None = None
    operation_id: str | None = None
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        missing_fields: list[str] = []
        if not self.request_id or self.request_id == _DEFAULT_REQUEST_ID:
            missing_fields.append("request_id")
        if not self.source or self.source == _DEFAULT_SOURCE:
            missing_fields.append("source")
        d.update(
            {
                "command_family": _command_family(self.command),
                "outcome_class": _outcome_class(self.policy_decision, self.exit_code),
                "risk_level": _risk_level(
                    command=self.command,
                    args=self.args,
                    policy_decision=self.policy_decision,
                    exit_code=self.exit_code,
                    write_mode=self.write_mode,
                    target_path=self.target_path,
                ),
                "is_network": _is_network_call(self.command, self.args),
                "is_write": _is_write_operation(self.command, self.args, self.write_mode, self.target_path),
                "http_method": _http_method(self.args),
                "target_host": _target_host(self.args),
                "has_stdin": self.stdin_bytes is not None and self.stdin_bytes > 0,
                "arg_fingerprint": _arg_fingerprint(self.command, self.args),
                "traceability_complete": len(missing_fields) == 0,
                "traceability_missing_fields": missing_fields,
            }
        )
        return d


# ---------------------------------------------------------------------------
# Logger setup
# ---------------------------------------------------------------------------

_LOG_DIR = Path(__file__).resolve().parents[3] / "logs" / "services"
_LOG_DIR.mkdir(parents=True, exist_ok=True)

_audit_logger = logging.getLogger("liara.sys_audit")
if not _audit_logger.handlers:
    _audit_logger.setLevel(logging.INFO)
    _audit_logger.propagate = False
    try:
        _handler = logging.FileHandler(_LOG_DIR / "sys_audit.jsonl", encoding="utf-8")
    except OSError:
        _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(_handler)


# ---------------------------------------------------------------------------
# Postgres repository delegation
# ---------------------------------------------------------------------------
#
# Hard cutover (no dual-write flag): once a PostgresSysAuditRepository is
# configured (i.e. running inside the API, where create_api_app() calls
# configure_sys_audit_repository() right after constructing one), the write
# functions below persist to Postgres only and skip the JSONL write.
# sys_audit.jsonl stays a live target only when no repository is configured
# (standalone scripts, existing unit tests that never call
# configure_sys_audit_repository) -- it is not a second writable store once
# the app is running.

_repo: "PostgresSysAuditRepository | None" = None


def configure_sys_audit_repository(repo: "PostgresSysAuditRepository | None") -> None:
    """Wire a PostgresSysAuditRepository into this module's write functions."""
    global _repo
    _repo = repo


def _delegate_to_repository(entry: "SysAuditEntry", entry_dict: dict[str, Any], *, fail_closed: bool = False) -> None:
    """Persist a terminal (blocked/executed) entry to the configured repository."""
    _repo.log_event_sync(
        tool_name=entry.command,
        lifecycle_stage="failed" if entry.policy_decision == "blocked" else "completed",
        outcome=entry.policy_decision,
        operation_id=entry.operation_id,
        proposal_id=entry.proposal_id,
        request_id=entry.request_id,
        session_id=entry.session_id,
        run_id=entry.run_id,
        context=entry.context,
        metadata=entry_dict,
        fail_closed=fail_closed,
        command=entry.command,
        command_family=entry_dict.get("command_family"),
        risk_level=entry_dict.get("risk_level"),
        is_write=entry_dict.get("is_write"),
        is_network=entry_dict.get("is_network"),
        duration_ms=entry.duration_ms,
    )


def _persist_entry(entry: "SysAuditEntry") -> dict[str, Any]:
    """Write a terminal entry to the configured Postgres repository if one is
    wired up (hard cutover, no dual-write); otherwise fall back to the JSONL
    logger (standalone scripts, tests that never call
    configure_sys_audit_repository)."""
    entry_dict = entry.to_dict()
    if _repo is not None:
        _delegate_to_repository(entry, entry_dict)
    else:
        _audit_logger.info(json.dumps(entry_dict))
    return entry_dict


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def log_blocked(
    command: str,
    args: list[str] | None,
    reason: str,
    *,
    stdin_text: str | None = None,
    target_path: str | None = None,
    storage_scope: str | None = None,
    retention_hint_seconds: int | None = None,
    write_mode: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    source: str | None = None,
    context: str | None = None,
    proposal_id: str | None = None,
    operation_id: str | None = None,
) -> SysAuditEntry:
    """Log a /sys request that was blocked by policy."""
    normalized_request_id = _derive_request_id(request_id=request_id, run_id=run_id, session_id=session_id)
    normalized_source = _normalize_source(_coalesce_traceability_fields(source, "orchestrator"))
    stdin_bytes, stdin_sha256 = _stdin_metadata(stdin_text)
    entry = SysAuditEntry(
        command=command,
        args=args,
        policy_decision="blocked",
        policy_reason=reason,
        exit_code=None,
        duration_ms=None,
        stdout_bytes=None,
        stderr_bytes=None,
        stdin_bytes=stdin_bytes,
        stdin_sha256=stdin_sha256,
        target_path=target_path,
        storage_scope=storage_scope,
        retention_hint_seconds=retention_hint_seconds,
        write_mode=write_mode,
        request_id=normalized_request_id,
        session_id=_normalize_optional_string(session_id),
        run_id=_normalize_optional_string(run_id),
        source=normalized_source,
        context=context,
        proposal_id=_normalize_optional_string(proposal_id),
        operation_id=operation_id,
    )
    _persist_entry(entry)
    return entry


def log_executed(
    command: str,
    args: list[str] | None,
    exit_code: int,
    duration_ms: float,
    stdout_bytes: int,
    stderr_bytes: int,
    *,
    stdin_text: str | None = None,
    target_path: str | None = None,
    storage_scope: str | None = None,
    retention_hint_seconds: int | None = None,
    write_mode: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    source: str | None = None,
    context: str | None = None,
    proposal_id: str | None = None,
    operation_id: str | None = None,
) -> SysAuditEntry:
    """Log a /sys request that passed policy and was executed."""
    normalized_request_id = _derive_request_id(request_id=request_id, run_id=run_id, session_id=session_id)
    normalized_source = _normalize_source(_coalesce_traceability_fields(source, "orchestrator"))
    stdin_bytes, stdin_sha256 = _stdin_metadata(stdin_text)
    entry = SysAuditEntry(
        command=command,
        args=args,
        policy_decision="allowed",
        policy_reason=None,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_bytes=stdout_bytes,
        stderr_bytes=stderr_bytes,
        stdin_bytes=stdin_bytes,
        stdin_sha256=stdin_sha256,
        target_path=target_path,
        storage_scope=storage_scope,
        retention_hint_seconds=retention_hint_seconds,
        write_mode=write_mode,
        request_id=normalized_request_id,
        session_id=_normalize_optional_string(session_id),
        run_id=_normalize_optional_string(run_id),
        source=normalized_source,
        context=context,
        proposal_id=_normalize_optional_string(proposal_id),
        operation_id=operation_id,
    )
    _persist_entry(entry)
    return entry


def log_judge_pre_action(
    *,
    tool_name: str,
    decision: str,
    issues: list[str] | None = None,
    constraints: dict[str, Any] | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    source: str | None = None,
    context: str | None = None,
) -> SysAuditEntry:
    """Log pre-action judge outcomes in sys_audit format.

    This keeps judge dispatch visibility in the same operational log stream as
    sys command executions, including pass-through handling for unprofiled tools.
    """
    decision_norm = (decision or "").strip().lower()
    blocked = decision_norm == "block"
    policy_reason = None
    if issues:
        policy_reason = "; ".join(str(issue) for issue in issues if str(issue).strip())
    if decision_norm == "revise" and not policy_reason:
        policy_reason = "Judge decision requires revision before safe execution."

    normalized_request_id = _derive_request_id(request_id=request_id, run_id=run_id, session_id=session_id)
    normalized_source = _normalize_source(_coalesce_traceability_fields(source, "orchestrator"))
    constraints = constraints or {}
    score_payload = constraints.get("validator_score") or constraints.get("score")
    risk_flags_payload = constraints.get("risk_flags")
    if not isinstance(score_payload, dict):
        score_payload = None
    if not isinstance(risk_flags_payload, list):
        risk_flags_payload = None

    entry = SysAuditEntry(
        command=f"judge:{(tool_name or '<unknown>').strip()}",
        args=None,
        policy_decision="blocked" if blocked else "allowed",
        policy_reason=policy_reason,
        exit_code=None if blocked else 0,
        duration_ms=None if blocked else 0.0,
        stdout_bytes=None if blocked else 0,
        stderr_bytes=None if blocked else 0,
        judge_score=score_payload,
        risk_flags=risk_flags_payload,
        judge_constraints=constraints or None,
        request_id=normalized_request_id,
        session_id=_normalize_optional_string(session_id),
        run_id=_normalize_optional_string(run_id),
        source=normalized_source,
        context=context or f"judge_pre_action:{decision_norm or 'unknown'}",
    )
    _persist_entry(entry)
    return entry


def log_started(
    command: str,
    args: list[str] | None,
    *,
    target_path: str | None = None,
    storage_scope: str | None = None,
    write_mode: str | None = None,
    request_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    source: str | None = None,
    context: str | None = None,
    proposal_id: str | None = None,
) -> str | None:
    """Fail-closed pre-action lifecycle event for a /sys command about to execute.

    Returns an operation_id to thread into the matching terminal
    log_executed() call, correlating the "started" and terminal events for
    the same attempt. Writes to the configured Postgres repository only --
    JSONL has no lifecycle concept to dual-write.

    When no repository is configured (standalone scripts, existing tests
    that never call configure_sys_audit_repository), this is a no-op that
    still returns an operation_id, so callers don't need two code paths.
    When a repository IS configured, a failed durable pre-action write
    raises AuditPersistenceError -- callers (SysExecutor.run) must treat
    that as fail-closed and not proceed to the external side effect.
    """
    operation_id = f"op-{uuid4().hex[:12]}"
    if _repo is None:
        return operation_id

    normalized_request_id = _derive_request_id(request_id=request_id, run_id=run_id, session_id=session_id)
    _repo.log_event_sync(
        tool_name=command,
        lifecycle_stage="started",
        operation_id=operation_id,
        proposal_id=_normalize_optional_string(proposal_id),
        request_id=normalized_request_id,
        session_id=_normalize_optional_string(session_id),
        run_id=_normalize_optional_string(run_id),
        context=context,
        metadata={
            "command": command,
            "args": args,
            "target_path": target_path,
            "storage_scope": storage_scope,
            "write_mode": write_mode,
        },
        fail_closed=True,
        command=command,
        command_family=_command_family(command),
    )
    return operation_id


def load_entries(path: Path | None = None, *, limit: int | None = None) -> list[dict[str, Any]]:
    """Load parsed audit entries from JSONL log with optional tail limit."""
    audit_path = path or (_LOG_DIR / "sys_audit.jsonl")
    if not audit_path.exists():
        return []
    if limit is not None and limit > 0:
        return _load_tail_entries(audit_path, limit)
    entries: list[dict[str, Any]] = []
    with audit_path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def count_entries(path: Path | None = None) -> int:
    """Count persisted JSONL records without decoding their payloads."""
    audit_path = path or (_LOG_DIR / "sys_audit.jsonl")
    if not audit_path.exists():
        return 0
    line_count = 0
    last_byte = b""
    with audit_path.open("rb") as fh:
        while chunk := fh.read(1024 * 1024):
            line_count += chunk.count(b"\n")
            last_byte = chunk[-1:]
    if last_byte and last_byte != b"\n":
        line_count += 1
    return line_count


def _load_tail_entries(audit_path: Path, limit: int) -> list[dict[str, Any]]:
    """Parse only the newest valid JSONL records instead of the full audit."""
    entries_reversed: list[dict[str, Any]] = []
    remainder = b""
    block_size = 64 * 1024
    with audit_path.open("rb") as fh:
        fh.seek(0, 2)
        position = fh.tell()
        while position > 0 and len(entries_reversed) < limit:
            read_size = min(block_size, position)
            position -= read_size
            fh.seek(position)
            data = fh.read(read_size) + remainder
            lines = data.split(b"\n")
            remainder = lines.pop(0) if position > 0 else b""
            for raw_line in reversed(lines):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                entries_reversed.append(entry)
                if len(entries_reversed) >= limit:
                    break
    return list(reversed(entries_reversed))


def summarize_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Return compact operational metrics for sys-audit analysis."""
    total = len(entries)
    blocked = sum(1 for e in entries if e.get("policy_decision") == "blocked")
    allowed = total - blocked
    failed_allowed = sum(1 for e in entries if e.get("policy_decision") == "allowed" and (e.get("exit_code") or 0) != 0)
    network_calls = sum(1 for e in entries if e.get("is_network") is True)
    write_ops = sum(1 for e in entries if e.get("is_write") is True)
    high_risk = sum(1 for e in entries if e.get("risk_level") == "high")
    durations = [float(e["duration_ms"]) for e in entries if e.get("duration_ms") is not None]
    avg_duration_ms = (sum(durations) / len(durations)) if durations else None
    by_source = Counter((e.get("source") or "unknown") for e in entries)
    by_context = Counter((e.get("context") or "unknown") for e in entries)
    return {
        "total": total,
        "allowed": allowed,
        "blocked": blocked,
        "failed_allowed": failed_allowed,
        "network_calls": network_calls,
        "write_ops": write_ops,
        "high_risk": high_risk,
        "avg_duration_ms": avg_duration_ms,
        "top_sources": by_source.most_common(5),
        "top_contexts": by_context.most_common(5),
    }


def filter_entries(
    entries: list[dict[str, Any]],
    *,
    blocked_only: bool = False,
    source: str | None = None,
    risk_level: str | None = None,
    command_family: str | None = None,
) -> list[dict[str, Any]]:
    """Filter audit entries by operational dimensions."""
    source_norm = (source or "").strip().lower()
    risk_norm = (risk_level or "").strip().lower()
    family_norm = (command_family or "").strip().lower()

    filtered: list[dict[str, Any]] = []
    for entry in entries:
        decision = str(entry.get("policy_decision") or "").lower()
        if blocked_only and decision != "blocked":
            continue

        entry_source = str(entry.get("source") or "unknown").lower()
        if source_norm and source_norm != "all" and entry_source != source_norm:
            continue

        entry_risk = str(entry.get("risk_level") or _risk_level(
            command=str(entry.get("command") or ""),
            args=entry.get("args") if isinstance(entry.get("args"), list) else None,
            policy_decision=decision,
            exit_code=entry.get("exit_code"),
            write_mode=entry.get("write_mode"),
            target_path=entry.get("target_path"),
        )).lower()
        if risk_norm and risk_norm != "all" and entry_risk != risk_norm:
            continue

        entry_family = str(entry.get("command_family") or _command_family(str(entry.get("command") or ""))).lower()
        if family_norm and family_norm != "all" and entry_family != family_norm:
            continue

        filtered.append(entry)

    return filtered


def find_suspicious_entries(entries: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    """Return the most relevant suspicious audit events for operator review."""
    suspicious: list[dict[str, Any]] = []
    for entry in entries:
        if entry.get("policy_decision") == "blocked":
            suspicious.append(entry)
            continue
        if entry.get("risk_level") == "high":
            suspicious.append(entry)
            continue
        if (entry.get("stderr_bytes") or 0) > 0:
            suspicious.append(entry)
            continue
        if (entry.get("duration_ms") or 0.0) >= 1000.0:
            suspicious.append(entry)
            continue
        if (entry.get("stdout_bytes") or 0) >= 50000:
            suspicious.append(entry)
    return suspicious[-limit:][::-1]


def _stdin_metadata(stdin_text: str | None) -> tuple[int | None, str | None]:
    if stdin_text is None:
        return None, None
    stdin_bytes = stdin_text.encode("utf-8")
    return len(stdin_bytes), hashlib.sha256(stdin_bytes).hexdigest()


def _command_family(command: str) -> str:
    command_lower = (command or "").lower()
    if command_lower == "venv-pip":
        return "dependency"
    if command_lower in {"curl", "wget"}:
        return "network"
    if command_lower in {"python", "python3"}:
        return "python"
    if command_lower in {"tee", "cp", "mv", "mkdir", "touch", "tar"}:
        return "filesystem"
    if command_lower in {"find", "ls", "grep", "head", "tail", "cat", "jq"}:
        return "inspection"
    return "other"


def _outcome_class(policy_decision: str, exit_code: int | None) -> str:
    if policy_decision == "blocked":
        return "blocked"
    if exit_code is None:
        return "unknown"
    return "success" if exit_code == 0 else "error"


def _is_network_call(command: str, args: list[str] | None) -> bool:
    command_lower = (command or "").lower()
    if command_lower in {"curl", "wget"}:
        return True
    if command_lower == "venv-pip":
        return bool(args and str(args[0]).lower() == "install")
    return any(str(arg).startswith(("http://", "https://")) for arg in (args or []))


def _http_method(args: list[str] | None) -> str | None:
    if not args:
        return None
    for index, arg in enumerate(args):
        if str(arg).upper() == "-X" and index + 1 < len(args):
            return str(args[index + 1]).upper()
    return "GET" if any(str(arg).startswith(("http://", "https://")) for arg in args) else None


def _target_host(args: list[str] | None) -> str | None:
    if not args:
        return None
    for arg in args:
        value = str(arg)
        if value.startswith(("http://", "https://")):
            parsed = urlparse(value)
            return parsed.netloc or None
    return None


def _is_write_operation(command: str, args: list[str] | None, write_mode: str | None, target_path: str | None) -> bool:
    if write_mode is not None or target_path is not None:
        return True
    command_lower = (command or "").lower()
    if command_lower in {"tee", "cp", "mv", "mkdir", "touch"}:
        return True
    method = _http_method(args)
    return method in {"POST", "PUT", "PATCH", "DELETE"}


def _risk_level(
    *,
    command: str,
    args: list[str] | None,
    policy_decision: str,
    exit_code: int | None,
    write_mode: str | None,
    target_path: str | None,
) -> str:
    if policy_decision == "blocked":
        return "high"
    if _is_write_operation(command, args, write_mode, target_path):
        return "high"
    if exit_code is not None and exit_code != 0:
        return "medium"
    if _is_network_call(command, args):
        return "medium"
    return "low"


def _arg_fingerprint(command: str, args: list[str] | None) -> str:
    payload = json.dumps({"command": command, "args": args or []}, ensure_ascii=True, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _normalize_request_id(request_id: str | None) -> str:
    if request_id is None:
        return _DEFAULT_REQUEST_ID
    normalized = request_id.strip()
    return normalized or _DEFAULT_REQUEST_ID


def _coalesce_traceability_fields(*values: str | None) -> str | None:
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def _derive_request_id(*, request_id: str | None, run_id: str | None, session_id: str | None) -> str:
    return _normalize_request_id(_coalesce_traceability_fields(request_id, run_id, session_id))


def _normalize_source(source: str | None) -> str:
    if source is None:
        return _DEFAULT_SOURCE
    normalized = source.strip()
    return normalized or _DEFAULT_SOURCE


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
