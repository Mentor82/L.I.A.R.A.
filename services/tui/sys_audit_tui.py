#!/usr/bin/env python3
"""LIARA Admin TUI — project audit viewer (Textual-first).

Reads and normalizes logs across the project (services/workers/tests/audits/demos)
and displays:
    1. Recent audit events (table)
    2. Blocked/error signal summary
    3. Top components by frequency

Usage:
  python -m services.tui.sys_audit_tui
    python -m services.tui.sys_audit_tui --scope project --limit 80 --follow
    python -m services.tui.sys_audit_tui --scope sys --blocked-only
"""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
import importlib
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from services.tools.builtin.sys_audit import (
    filter_entries as filter_sys_audit_entries,
    find_suspicious_entries,
)

try:
    import httpx
    _HTTPX_AVAILABLE = True
except ImportError:
    _HTTPX_AVAILABLE = False


def _audit_log_path() -> Path:
    return Path(__file__).resolve().parents[2] / "logs" / "services" / "sys_audit.jsonl"


def _project_logs_root() -> Path:
    return Path(__file__).resolve().parents[2] / "logs"


_API_BASE_ENV = "LIARA_API_BASE_URL"
_API_BASE_DEFAULT = "http://127.0.0.1:8010"
_HTTP_FETCH_LIMIT_DEFAULT = 2000
_HTTP_TIMEOUT_SECONDS = 5.0


def _api_base_url() -> str:
    return (os.getenv(_API_BASE_ENV) or _API_BASE_DEFAULT).rstrip("/")


def _http_auth_headers() -> dict[str, str]:
    token = os.getenv("LIARA_ADMIN_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _fetch_sys_entries_via_http(*, limit: int = _HTTP_FETCH_LIMIT_DEFAULT) -> list[dict[str, Any]] | None:
    """Fetch raw sys-audit entries from the admin API.

    Returns None on any failure (unreachable API, non-200, malformed
    payload) so the caller falls back to the local JSONL file -- mirrors
    admin_tui/data_layer.py's HTTP-first-then-file convention.
    """
    if not _HTTPX_AVAILABLE:
        return None
    try:
        resp = httpx.get(
            f"{_api_base_url()}/admin/sys-audit/events",
            params={"limit": limit},
            headers=_http_auth_headers(),
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
        if resp.status_code != 200:
            return None
        payload = resp.json()
        items = payload.get("items")
        if not isinstance(items, list):
            return None
        return items
    except Exception:
        return None


def _load_sys_entries(log_path: Path, *, explicit_log_path: bool) -> list[dict[str, Any]]:
    """Load 'sys'-scope audit entries: HTTP-first against the admin API,
    falling back to the local JSONL file.

    An explicit --log path always reads the file directly (dev/debug escape
    hatch, same convention as the /admin/sys-audit/* log_path override).
    Production mode also disables the file fallback, mirroring
    admin_tui/data_layer.py, so an unreachable API in production yields no
    entries rather than silently reading a possibly-stale local file.
    """
    if not explicit_log_path:
        http_items = _fetch_sys_entries_via_http()
        if http_items is not None:
            return [
                _normalize_json_entry(item, path=log_path)
                for item in http_items
                if isinstance(item, dict)
            ]
        if (os.getenv("LIARA_ENV", "development").strip().lower()) == "production":
            return []
    return _load_entries(log_path)


_EVENT_LOG_MAX_LINES_DEFAULT = 2000
_EVENT_LOG_MAX_LINES_ENV = "LIARA_SYS_AUDIT_TUI_MAX_EVENT_LINES"


def _event_log_max_lines() -> int:
    raw = (os.getenv(_EVENT_LOG_MAX_LINES_ENV) or "").strip()
    if not raw:
        return _EVENT_LOG_MAX_LINES_DEFAULT
    try:
        parsed = int(raw)
    except ValueError:
        return _EVENT_LOG_MAX_LINES_DEFAULT
    if parsed <= 0:
        return _EVENT_LOG_MAX_LINES_DEFAULT
    return parsed


def _should_emit_refresh_message(previous: str | None, current: str) -> bool:
    if not current:
        return False
    return previous != current


_PROJECT_DOMAINS: tuple[str, ...] = (
    "services",
    "workers",
    "tests",
    "audits",
    "demos",
    "tex-ui",
    "infra",
)


def _discover_project_log_files(
    root: Path,
    *,
    max_files: int = 400,
) -> list[Path]:
    if not root.exists():
        return []

    candidates: list[Path] = []
    for domain in _PROJECT_DOMAINS:
        domain_path = root / domain
        if not domain_path.exists():
            continue
        for pattern in ("**/*.jsonl", "**/*.json", "**/*.log", "**/*.err", "**/*.out", "**/*.env", "**/*.done"):
            candidates.extend(domain_path.glob(pattern))

    filtered = [p for p in candidates if p.is_file() and p.name != ".gitkeep"]
    dedup_sorted = sorted(set(filtered), key=lambda p: p.stat().st_mtime if p.exists() else 0.0, reverse=True)
    return dedup_sorted[:max_files]


def _tail_lines(path: Path, *, limit: int) -> list[str]:
    lines: deque[str] = deque(maxlen=max(1, limit))
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            text = line.strip()
            if text:
                lines.append(text)
    return list(lines)


def _domain_from_path(path: Path) -> str:
    parts = [part.lower() for part in path.parts]
    if "logs" in parts:
        idx = parts.index("logs")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    for domain in _PROJECT_DOMAINS:
        if domain in parts:
            return domain
    if path.parent.name:
        return path.parent.name.lower()
    return "unknown"


def _severity_from_entry(entry: dict[str, Any]) -> str:
    decision = str(entry.get("policy_decision") or "").lower()
    if decision == "blocked":
        return "high"

    exit_code = entry.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return "high"

    explicit_risk = str(entry.get("risk_level") or "").lower()
    if explicit_risk in {"low", "medium", "high"}:
        return explicit_risk

    text = " ".join(
        str(value)
        for value in (
            entry.get("policy_reason"),
            entry.get("message"),
            entry.get("command"),
            entry.get("source"),
        )
        if value is not None
    ).lower()
    if re.search(r"\b(block|denied|error|fail|exception|timeout|critical|panic)\b", text):
        return "high"
    if re.search(r"\b(warn|retry|degrad|fallback)\b", text):
        return "medium"
    return "low"


def _coerce_timestamp(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _normalize_json_entry(raw: dict[str, Any], *, path: Path) -> dict[str, Any]:
    entry = dict(raw)
    entry.setdefault("source", _domain_from_path(path))
    entry.setdefault("domain", _domain_from_path(path))
    entry.setdefault("component", path.stem)
    entry.setdefault("file", str(path))

    if "policy_decision" not in entry:
        status_text = " ".join(
            str(entry.get(key) or "")
            for key in ("status", "result", "level", "severity", "message", "error")
        ).lower()
        if re.search(r"\b(block|denied|error|fail|critical|exception)\b", status_text):
            entry["policy_decision"] = "blocked"
        else:
            entry["policy_decision"] = "allowed"

    if "policy_reason" not in entry:
        reason = entry.get("error") or entry.get("message") or entry.get("detail")
        entry["policy_reason"] = None if reason in (None, "") else str(reason)

    metrics = None
    if isinstance(entry.get("reasoning_metrics"), dict):
        metrics = dict(entry.get("reasoning_metrics") or {})
    elif isinstance(entry.get("metadata"), dict):
        metadata = dict(entry.get("metadata") or {})
        if isinstance(metadata.get("reasoning_metrics"), dict):
            metrics = dict(metadata.get("reasoning_metrics") or {})
        elif isinstance(metadata.get("debug_run"), dict) and isinstance((metadata.get("debug_run") or {}).get("reasoning_metrics"), dict):
            metrics = dict((metadata.get("debug_run") or {}).get("reasoning_metrics") or {})
    # Direct top-level metrics fields (e.g. from reasoning_metrics_runtime_audit.json results)
    if metrics is None and (
        isinstance(entry.get("backend"), str)
        or isinstance(entry.get("compute_path"), str)
        or entry.get("rds_v2") is not None
    ):
        metrics = {
            "compute_backend": entry.get("backend"),
            "compute_path": entry.get("compute_path"),
            "fallback_reason": entry.get("fallback_reason"),
            "total_cost": entry.get("total_cost"),
            "rds_v2": entry.get("rds_v2"),
            "total_risk": entry.get("total_risk"),
        }

    if metrics:
        # "compute_backend" is used in nested dicts; direct audit files use "backend"
        entry["reasoning_metrics_backend"] = metrics.get("compute_backend") or metrics.get("backend")
        entry["reasoning_metrics_path"] = metrics.get("compute_path")
        entry["reasoning_metrics_fallback_reason"] = metrics.get("fallback_reason")
        entry["reasoning_metrics_total_cost"] = metrics.get("total_cost")
        entry["reasoning_metrics_rds_v2"] = metrics.get("rds_v2")
        entry["reasoning_metrics_total_risk"] = metrics.get("total_risk")
        entry.setdefault("component", "reasoning_metrics")
        entry.setdefault("command", "reasoning_metrics")
        if entry.get("reasoning_metrics_fallback_reason") and not entry.get("policy_reason"):
            entry["policy_reason"] = f"reasoning_metrics fallback: {entry.get('reasoning_metrics_fallback_reason')}"

    entry["timestamp"] = _coerce_timestamp(entry.get("timestamp") or entry.get("ts") or entry.get("created_at"))
    entry["risk_level"] = _severity_from_entry(entry)
    return entry


def _reasoning_metrics_label(entry: dict[str, Any]) -> str:
    backend = str(entry.get("reasoning_metrics_backend") or "").strip()
    path = str(entry.get("reasoning_metrics_path") or "").strip()
    if not backend and not path:
        return "-"
    if backend and path:
        return f"{backend}/{path}"
    return backend or path


def _request_id_value(entry: dict[str, Any]) -> str:
    direct = entry.get("request_id")
    if direct not in (None, ""):
        return str(direct)
    metadata = entry.get("metadata")
    if isinstance(metadata, dict):
        nested = metadata.get("request_id")
        if nested not in (None, ""):
            return str(nested)
    trace = entry.get("trace")
    if isinstance(trace, dict):
        nested = trace.get("request_id")
        if nested not in (None, ""):
            return str(nested)
    return "-"


def _short_request_id(entry: dict[str, Any]) -> str:
    request_id = _request_id_value(entry)
    if request_id == "-":
        return "-"
    if len(request_id) <= 12:
        return request_id
    return request_id[:12]


def _normalize_text_line_entry(*, path: Path, line: str) -> dict[str, Any]:
    lowered = line.lower()
    decision = "blocked" if re.search(r"\b(block|denied|error|fail|exception|critical|panic)\b", lowered) else "allowed"
    command = path.stem
    return {
        "command": command,
        "args": None,
        "policy_decision": decision,
        "policy_reason": line if decision == "blocked" else None,
        "exit_code": 1 if decision == "blocked" else 0,
        "duration_ms": None,
        "stdout_bytes": None,
        "stderr_bytes": None,
        "timestamp": None,
        "source": _domain_from_path(path),
        "domain": _domain_from_path(path),
        "component": path.stem,
        "file": str(path),
        "message": line,
        "risk_level": "high" if decision == "blocked" else "low",
    }


def _load_project_entries(
    root: Path,
    *,
    max_files: int = 400,
    max_lines_per_file: int = 80,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in _discover_project_log_files(root, max_files=max_files):
        suffix = path.suffix.lower()
        try:
            if suffix == ".jsonl":
                for line in _tail_lines(path, limit=max_lines_per_file):
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        entries.append(_normalize_text_line_entry(path=path, line=line))
                        continue
                    if isinstance(payload, dict):
                        entries.append(_normalize_json_entry(payload, path=path))
                    elif isinstance(payload, list):
                        entries.append(
                            _normalize_json_entry(
                                {"message": f"list-entry size={len(payload)}", "status": "info"},
                                path=path,
                            )
                        )
            elif suffix == ".json":
                raw = path.read_text(encoding="utf-8", errors="replace")
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    report_results = payload.get("results")
                    if isinstance(report_results, list) and report_results:
                        for item in report_results[-max_lines_per_file:]:
                            if isinstance(item, dict):
                                merged_item = dict(item)
                                if "timestamp" not in merged_item and payload.get("generated_at") is not None:
                                    merged_item["timestamp"] = payload.get("generated_at")
                                if "source" not in merged_item:
                                    merged_item["source"] = "reasoning_metrics_audit"
                                entries.append(_normalize_json_entry(merged_item, path=path))
                            else:
                                entries.append(_normalize_text_line_entry(path=path, line=str(item)))
                    else:
                        entries.append(_normalize_json_entry(payload, path=path))
                elif isinstance(payload, list):
                    for item in payload[-max_lines_per_file:]:
                        if isinstance(item, dict):
                            entries.append(_normalize_json_entry(item, path=path))
                        else:
                            entries.append(_normalize_text_line_entry(path=path, line=str(item)))
                else:
                    entries.append(_normalize_text_line_entry(path=path, line=str(payload)))
            else:
                for line in _tail_lines(path, limit=max_lines_per_file):
                    entries.append(_normalize_text_line_entry(path=path, line=line))
        except Exception as exc:
            entries.append(
                _normalize_json_entry(
                    {
                        "status": "error",
                        "message": f"failed to parse log file: {path.name}",
                        "error": str(exc),
                    },
                    path=path,
                )
            )

    entries.sort(key=lambda item: item.get("timestamp") or 0.0, reverse=False)
    return entries


def _load_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    payload = json.loads(line)
                    if isinstance(payload, dict):
                        entries.append(_normalize_json_entry(payload, path=path))
                    else:
                        entries.append(_normalize_text_line_entry(path=path, line=line))
                except json.JSONDecodeError:
                    entries.append(_normalize_text_line_entry(path=path, line=line))
    return entries


def _format_ts(ts: float | None) -> str:
    if ts is None:
        return "-"
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _fmt_decision(decision: str) -> str:
    if decision == "blocked":
        return "[bold red]BLOCKED[/bold red]"
    return "[green]ok[/green]"


def _collect_stats(entries: list[dict]) -> dict[str, Any]:
    if not entries:
        return {
            "total": 0,
            "allowed": 0,
            "blocked": 0,
            "failed_allowed": 0,
            "network_calls": 0,
            "write_ops": 0,
            "high_risk": 0,
            "missing_request_id": 0,
            "missing_source": 0,
            "traceability_complete": 0,
            "avg_duration_ms": None,
            "domains": 0,
        }

    total = len(entries)
    blocked = sum(1 for e in entries if str(e.get("policy_decision") or "").lower() == "blocked")
    allowed = total - blocked
    failed_allowed = sum(
        1
        for e in entries
        if str(e.get("policy_decision") or "").lower() == "allowed"
        and isinstance(e.get("exit_code"), int)
        and e.get("exit_code") != 0
    )
    network_calls = sum(1 for e in entries if e.get("is_network") is True)
    write_ops = sum(1 for e in entries if e.get("is_write") is True)
    high_risk = sum(1 for e in entries if str(e.get("risk_level") or "").lower() == "high")
    missing_request_id = sum(1 for e in entries if _request_id_value(e) == "-")
    missing_source = sum(1 for e in entries if str(e.get("source") or "").strip() in {"", "unknown"})
    traceability_complete = sum(1 for e in entries if str(e.get("traceability_complete") or "").lower() == "true")
    durations = [float(duration) for e in entries if (duration := e.get("duration_ms")) is not None]
    domains = len({str(e.get("domain") or e.get("source") or "unknown") for e in entries})

    return {
        "total": total,
        "allowed": allowed,
        "blocked": blocked,
        "failed_allowed": failed_allowed,
        "network_calls": network_calls,
        "write_ops": write_ops,
        "high_risk": high_risk,
        "missing_request_id": missing_request_id,
        "missing_source": missing_source,
        "traceability_complete": traceability_complete,
        "avg_duration_ms": (sum(durations) / len(durations)) if durations else None,
        "domains": domains,
    }


def _filtered_recent(entries: list[dict], limit: int) -> list[dict]:
    return entries[-limit:][::-1]


def _apply_filters(
    entries: list[dict],
    *,
    blocked_only: bool,
    source_filter: str | None,
    risk_filter: str | None,
    family_filter: str | None,
    domain_filter: str | None = None,
) -> list[dict]:
    filtered = filter_sys_audit_entries(
        entries,
        blocked_only=blocked_only,
        source=source_filter,
        risk_level=risk_filter,
        command_family=family_filter,
    )
    domain_norm = _filter_label(domain_filter).lower()
    if domain_norm in {"", "all"}:
        return filtered
    return [
        entry
        for entry in filtered
        if str(entry.get("domain") or entry.get("source") or "unknown").lower() == domain_norm
    ]


def _filter_label(value: str | None) -> str:
    value_norm = (value or "all").strip() or "all"
    return value_norm


def _reason_counter(entries: list[dict]) -> Counter[str]:
    blocked = [e for e in entries if e.get("policy_decision") == "blocked"]
    counter: Counter[str] = Counter()
    for e in blocked:
        counter[e.get("policy_reason") or "unknown"] += 1
    return counter


def _top_commands(entries: list[dict], max_items: int = 10) -> list[tuple[str, int, int, int]]:
    cmd_counter: Counter[str] = Counter(e.get("command", "?") for e in entries)
    blocked_by_cmd: Counter[str] = Counter(
        e.get("command", "?") for e in entries if e.get("policy_decision") == "blocked"
    )
    rows: list[tuple[str, int, int, int]] = []
    for cmd, total in cmd_counter.most_common(max_items):
        blocked = blocked_by_cmd.get(cmd, 0)
        rows.append((cmd, total, blocked, total - blocked))
    return rows


def _top_sources(entries: list[dict], max_items: int = 8) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter((e.get("source") or "unknown") for e in entries)
    return counter.most_common(max_items)


def _top_domains(entries: list[dict], max_items: int = 8) -> list[tuple[str, int]]:
    counter: Counter[str] = Counter((e.get("domain") or e.get("source") or "unknown") for e in entries)
    return counter.most_common(max_items)


def _missing_traceability_fields(entry: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if _request_id_value(entry) == "-":
        missing.append("request_id")

    source = str(entry.get("source") or "").strip().lower()
    if source in {"", "unknown"}:
        missing.append("source")

    context = str(entry.get("context") or "").strip().lower()
    if context in {"", "unknown"}:
        missing.append("context")

    return missing


def _traceability_drilldown(entries: list[dict], *, max_items: int = 8) -> dict[str, Any]:
    missing_sources: Counter[str] = Counter()
    missing_contexts: Counter[str] = Counter()
    missing_memory_ops: Counter[str] = Counter()
    missing_fields: Counter[str] = Counter()
    incomplete_count = 0

    for entry in entries:
        missing = _missing_traceability_fields(entry)
        if not missing:
            continue
        incomplete_count += 1
        source_key = str(entry.get("source") or "unknown").strip() or "unknown"
        context_key = str(entry.get("context") or "unknown").strip() or "unknown"
        missing_sources[source_key] += 1
        missing_contexts[context_key] += 1
        memory_op = _memory_operation_cluster(entry)
        if memory_op is not None:
            missing_memory_ops[memory_op] += 1
        for field in missing:
            missing_fields[field] += 1

    return {
        "incomplete_count": incomplete_count,
        "top_missing_sources": missing_sources.most_common(max_items),
        "top_missing_contexts": missing_contexts.most_common(max_items),
        "top_missing_memory_ops": missing_memory_ops.most_common(max_items),
        "missing_fields": missing_fields,
    }


def _memory_operation_cluster(entry: dict[str, Any]) -> str | None:
    command = str(entry.get("command") or "").strip().lower()
    context = str(entry.get("context") or "").strip().lower()
    source = str(entry.get("source") or "").strip().lower()
    args = [str(arg).strip().lower() for arg in (entry.get("args") or [])]

    if context.startswith("memory."):
        context_parts = [part for part in context.split(".") if part]
        if len(context_parts) >= 3:
            return ".".join(context_parts[:3])
        return context

    if command == "memory":
        if args:
            op = args[0].replace("_", ".")
            if op.startswith("staging"):
                return f"memory.{op}"
            if op.startswith("dreaming"):
                return f"memory.{op}"
            return f"memory.{op}"
        return "memory.unknown"

    if source in {"memory_service", "memory-service", "memory"}:
        return "memory.unknown"

    return None


def _stringify_args(args: Any) -> str:
    if isinstance(args, (list, tuple)):
        return " ".join(str(item) for item in args) or "-"
    if args in (None, ""):
        return "-"
    return str(args)


def _operator_signal(entry: dict[str, Any]) -> str:
    command = str(entry.get("command") or "").strip().lower()
    context = str(entry.get("context") or "").strip().lower()
    reason = str(entry.get("policy_reason") or "").strip().lower()
    risk_flags = [str(flag).strip().lower() for flag in (entry.get("risk_flags") or []) if str(flag).strip()]
    score_total = None
    judge_score = entry.get("judge_score")
    if isinstance(judge_score, dict):
        score_total = judge_score.get("gesamt")
    metrics_path = str(entry.get("reasoning_metrics_path") or "").strip().lower()
    metrics_backend = str(entry.get("reasoning_metrics_backend") or "").strip().lower()

    if metrics_path == "fallback":
        return "metrics_fallback"
    if metrics_backend == "julia" and metrics_path == "primary":
        return "metrics_primary"

    if command == "judge:fact_lookup_reference" and context == "logic_error_missing_knowledge_reference":
        return "fact_lookup_reference_guard"
    if "formula_mismatch" in risk_flags:
        return "validator_formula_mismatch"
    if "unit_mismatch" in risk_flags:
        return "validator_unit_mismatch"
    if "logic_branch_dead" in risk_flags:
        return "validator_dead_branch"
    if "crash_without_try_except" in risk_flags:
        return "validator_crash_risk"
    if isinstance(score_total, (int, float)):
        if float(score_total) >= 4.5:
            return "validator_grade_fail"
        if float(score_total) >= 3.5:
            return "validator_grade_warn"
    if command.startswith("judge:") and "missing [knowledge_reference]" in reason:
        return "missing_knowledge_reference"
    if command.startswith("judge:") and context.startswith("judge_pre_action:block"):
        return "judge_block"
    if command.startswith("judge:") and context.startswith("judge_pre_action:revise"):
        return "judge_revise"
    if str(entry.get("policy_decision") or "").lower() == "blocked":
        return "blocked_event"
    if str(entry.get("risk_level") or "").lower() == "high":
        return "high_risk"
    return "normal"


def _entry_detail_pairs(entry: dict[str, Any]) -> list[tuple[str, str]]:
    raw_json = json.dumps(entry, indent=2, ensure_ascii=False, default=str)
    timestamp = entry.get("timestamp")
    duration = entry.get("duration_ms")
    judge_score = entry.get("judge_score") if isinstance(entry.get("judge_score"), dict) else None
    risk_flags = entry.get("risk_flags") if isinstance(entry.get("risk_flags"), list) else None
    judge_constraints = entry.get("judge_constraints") if isinstance(entry.get("judge_constraints"), dict) else None

    score_text = "-"
    if judge_score:
        note = str(judge_score.get("note_text") or "-")
        gesamt = judge_score.get("gesamt")
        fach = judge_score.get("fach")
        code = judge_score.get("code")
        robustheit = judge_score.get("robustheit")
        confidence = judge_score.get("confidence")
        parts = [
            f"gesamt={gesamt}" if gesamt is not None else "gesamt=-",
            f"note={note}",
            f"fach={fach}" if fach is not None else "fach=-",
            f"code={code}" if code is not None else "code=-",
            f"robustheit={robustheit}" if robustheit is not None else "robustheit=-",
            f"confidence={confidence}" if confidence is not None else "confidence=-",
        ]
        score_text = ", ".join(parts)

    risk_flags_text = "-"
    if risk_flags:
        risk_flags_text = ", ".join(str(flag) for flag in risk_flags)

    constraints_text = "-"
    if judge_constraints:
        constraints_text = json.dumps(judge_constraints, ensure_ascii=False, default=str)

    metrics_backend = str(entry.get("reasoning_metrics_backend") or "-")
    metrics_path = str(entry.get("reasoning_metrics_path") or "-")
    metrics_fallback = str(entry.get("reasoning_metrics_fallback_reason") or "-")
    metrics_cost = entry.get("reasoning_metrics_total_cost")
    metrics_rds = entry.get("reasoning_metrics_rds_v2")
    metrics_risk = entry.get("reasoning_metrics_total_risk")
    metrics_summary = (
        f"backend={metrics_backend}, path={metrics_path}, "
        f"total_cost={metrics_cost if metrics_cost is not None else '-'}, "
        f"rds_v2={metrics_rds if metrics_rds is not None else '-'}, "
        f"total_risk={metrics_risk if metrics_risk is not None else '-'}"
    )

    return [
        ("Time", _format_ts(timestamp) if timestamp is not None else "-"),
        ("Signal", _operator_signal(entry)),
        ("Decision", str(entry.get("policy_decision") or "-")),
        ("Risk", str(entry.get("risk_level") or "-")),
        ("Domain", str(entry.get("domain") or entry.get("source") or "-")),
        ("Source", str(entry.get("source") or "-")),
        ("Request ID", _request_id_value(entry)),
        ("Context", str(entry.get("context") or "-")),
        ("Component", str(entry.get("component") or entry.get("command") or "-")),
        ("Command", str(entry.get("command") or "-")),
        ("Args", _stringify_args(entry.get("args"))),
        ("Exit", "-" if entry.get("exit_code") is None else str(entry.get("exit_code"))),
        ("Duration", "-" if duration is None else f"{float(duration):.1f} ms"),
        ("Judge Score", score_text),
        ("Risk Flags", risk_flags_text),
        ("Judge Constraints", constraints_text),
        ("Reasoning Metrics", metrics_summary),
        ("RM Fallback Reason", metrics_fallback),
        ("Reason", str(entry.get("policy_reason") or "-")),
        ("Message", str(entry.get("message") or "-")),
        ("File", str(entry.get("file") or "-")),
        ("Raw JSON", raw_json),
    ]


def _monitoring_status_line(
    *,
    scope: str,
    total_entries: int,
    filtered_entries: int,
    suspicious_count: int,
    blocked_count: int,
    missing_request_id_count: int,
    auto_refresh_enabled: bool,
    refresh_duration_ms: float | None,
    last_refresh_label: str,
    last_error: str | None,
) -> str:
    refresh_mode = "on" if auto_refresh_enabled else "off"
    refresh_text = "-" if refresh_duration_ms is None else f"{refresh_duration_ms:.1f} ms"
    error_text = last_error or "none"
    return (
        f"[bold cyan]Monitoring[/bold cyan]: scope={scope} auto-refresh={refresh_mode} "
        f"last-refresh={last_refresh_label} refresh-time={refresh_text}  "
        f"[bold cyan]Visible[/bold cyan]: {filtered_entries}/{total_entries}  "
        f"[red]Blocked[/red]: {blocked_count}  "
        f"[yellow]Suspicious[/yellow]: {suspicious_count}  "
        f"[magenta]Missing request_id[/magenta]: {missing_request_id_count}  "
        f"[bold cyan]Last error[/bold cyan]: {error_text}"
    )


def _load_textual_symbols() -> tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any, Any]:
    """Load Textual classes lazily with a friendly install hint."""
    try:
        app_mod = importlib.import_module("textual.app")
        binding_mod = importlib.import_module("textual.binding")
        containers_mod = importlib.import_module("textual.containers")
        screen_mod = importlib.import_module("textual.screen")
        widgets_mod = importlib.import_module("textual.widgets")
    except ImportError as exc:
        raise RuntimeError(
            "textual is required for services.tui.sys_audit_tui. "
            "Install it with: pip install textual"
        ) from exc

    App = getattr(app_mod, "App")
    Binding = getattr(binding_mod, "Binding")
    Vertical = getattr(containers_mod, "Vertical")
    Horizontal = getattr(containers_mod, "Horizontal")
    ModalScreen = getattr(screen_mod, "ModalScreen")
    Header = getattr(widgets_mod, "Header")
    Footer = getattr(widgets_mod, "Footer")
    Static = getattr(widgets_mod, "Static")
    RichLog = getattr(widgets_mod, "RichLog")
    DataTable = getattr(widgets_mod, "DataTable")
    Button = getattr(widgets_mod, "Button")
    Label = getattr(widgets_mod, "Label")
    TextArea = getattr(widgets_mod, "TextArea")

    return App, Binding, Vertical, Horizontal, ModalScreen, Header, Footer, Static, RichLog, DataTable, Button, Label, TextArea


def create_sys_audit_app(
    log_path: Path,
    limit: int,
    blocked_only: bool,
    interval_seconds: float,
    *,
    scope: str = "project",
    source_filter: str | None = None,
    risk_filter: str | None = None,
    family_filter: str | None = None,
    domain_filter: str | None = None,
    max_files: int = 400,
    max_lines_per_file: int = 80,
    explicit_log_path: bool = False,
):
    """Create Textual app class for live sys-audit inspection."""
    App, Binding, Vertical, Horizontal, ModalScreen, Header, Footer, Static, RichLog, DataTable, Button, Label, TextArea = _load_textual_symbols()

    class DetailModal(ModalScreen):
        DEFAULT_CSS = """
        DetailModal {
            align: center middle;
        }

        DetailModal > Vertical {
            width: 88%;
            max-height: 88%;
            background: $surface;
            border: solid $primary;
            padding: 0 1;
        }

        DetailModal #detail-title-bar {
            height: 3;
            align: left middle;
            background: $primary;
            padding: 0 1;
        }

        DetailModal #detail-title {
            width: 1fr;
            text-style: bold;
            color: $text;
        }

        DetailModal #detail-close {
            width: 5;
            min-width: 5;
        }

        DetailModal #detail-body {
            height: 1fr;
            padding: 1 0;
            overflow-y: auto;
        }

        DetailModal .field-label {
            text-style: bold;
            color: $accent;
        }

        DetailModal .field-value {
            margin-bottom: 1;
        }

        DetailModal .field-area {
            height: 8;
            border: solid $primary-darken-2;
            background: $surface-darken-1;
            margin-bottom: 1;
        }
        """

        def __init__(self, entry: dict[str, Any]) -> None:
            super().__init__()
            self._entry = entry

        def compose(self):
            title = str(self._entry.get("component") or self._entry.get("command") or "audit-entry")
            with Vertical():
                with Horizontal(id="detail-title-bar"):
                    yield Label(f"Audit Entry View: {title}", id="detail-title")
                    yield Button("Close", id="detail-close")
                with Vertical(id="detail-body"):
                    for label, value in _entry_detail_pairs(self._entry):
                        yield Static(label, classes="field-label")
                        if label in {"Reason", "Message", "Raw JSON"}:
                            yield TextArea(value, classes="field-area", read_only=True)
                        else:
                            yield Static(value, classes="field-value")

        def on_button_pressed(self, event: Any) -> None:
            if event.button.id == "detail-close":
                self.dismiss()

        def on_key(self, event) -> None:
            if event.key == "escape":
                self.dismiss()

    class SysAuditApp(App):
        TITLE = "LIARA Project Audit"
        SUB_TITLE = "Live cross-domain operational audit"

        CSS = """
        Screen {
            layout: vertical;
        }

        #main {
            height: 1fr;
        }

        #stats {
            height: auto;
            border: solid #3d4c63;
            padding: 1 2;
        }

        #monitoring {
            height: auto;
            border: solid #30506e;
            padding: 1 2;
        }

        #toolbar {
            height: auto;
            padding: 0 0 1 0;
        }

        .toolbar-button {
            margin-right: 1;
        }

        #recent {
            height: 40%;
        }

        #split {
            height: 28%;
        }

        #blocked {
            width: 1fr;
        }

        #top {
            width: 1fr;
        }

        #events {
            height: 28%;
            border: solid #3d4c63;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh"),
            Binding("a", "toggle_auto_refresh", "Auto refresh"),
            Binding("v", "view_entry", "View entry"),
            Binding("b", "toggle_blocked", "Blocked only"),
            Binding("s", "cycle_source", "Source"),
            Binding("k", "cycle_risk", "Risk"),
            Binding("f", "cycle_family", "Family"),
            Binding("d", "cycle_domain", "Domain"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.log_path = log_path
            self.scope = (scope or "project").strip().lower()
            self.limit = max(1, limit)
            self.blocked_only = blocked_only
            self.interval_seconds = max(0.5, interval_seconds)
            self.source_filter = _filter_label(source_filter)
            self.risk_filter = _filter_label(risk_filter)
            self.family_filter = _filter_label(family_filter)
            self.domain_filter = _filter_label(domain_filter)
            self.max_files = max(1, max_files)
            self.max_lines_per_file = max(1, max_lines_per_file)
            self.explicit_log_path = explicit_log_path
            self._inflight = False
            self._auto_refresh_enabled = True
            self._current_recent_entries: list[dict[str, Any]] = []
            self._refresh_count = 0
            self._last_refresh_label = "never"
            self._last_refresh_duration_ms: float | None = None
            self._event_log_max_lines = _event_log_max_lines()
            self._last_refresh_message: str | None = None
            self._last_error: str | None = None

        def _source_cycle_options(self, entries: list[dict]) -> list[str]:
            sources = sorted({str(e.get("source") or "unknown") for e in entries})
            return ["all", *sources] if sources else ["all"]

        @staticmethod
        def _cycle(current: str, options: list[str]) -> str:
            if not options:
                return "all"
            if current not in options:
                return options[0]
            idx = options.index(current)
            return options[(idx + 1) % len(options)]

        def compose(self):
            yield Header(show_clock=True)
            with Vertical(id="main"):
                yield Static(id="stats")
                yield Static(id="monitoring")
                with Horizontal(id="toolbar"):
                    yield Button("Refresh now", id="refresh-now", classes="toolbar-button")
                    yield Button("Auto refresh: on", id="toggle-auto-refresh", classes="toolbar-button")
                    yield Button("View selection", id="view-entry", classes="toolbar-button")
                yield DataTable(id="recent", cursor_type="row")
                with Horizontal(id="split"):
                    yield DataTable(id="blocked", cursor_type="row")
                    yield DataTable(id="top", cursor_type="row")
                yield RichLog(id="events", highlight=True, wrap=True, max_lines=self._event_log_max_lines)
            yield Footer()

        def on_mount(self) -> None:
            recent = self.query_one("#recent", DataTable)
            recent.add_columns("Time", "Signal", "Decision", "Risk", "Domain", "Component", "Command", "ReqId", "RM", "Args", "Exit", "ms", "Source", "Reason")
            recent.zebra_stripes = True

            blocked = self.query_one("#blocked", DataTable)
            blocked.add_columns("Blocked Reason", "Count")
            blocked.zebra_stripes = True

            top = self.query_one("#top", DataTable)
            top.add_columns("Component", "Total", "Blocked", "Allowed")
            top.zebra_stripes = True

            events = self.query_one("#events", RichLog)
            if self.scope == "project":
                events.write(f"[cyan]Monitoring scope[/cyan]: project @ {_project_logs_root()}")
            else:
                events.write(f"[cyan]Monitoring scope[/cyan]: sys @ {self.log_path}")
            mode = "on" if self.blocked_only else "off"
            events.write(f"[yellow]blocked-only[/yellow]: {mode}")
            events.write(
                f"[yellow]filters[/yellow]: source={self.source_filter} risk={self.risk_filter} family={self.family_filter} domain={self.domain_filter}"
            )

            self._update_auto_refresh_button()
            self.set_interval(self.interval_seconds, self._auto_refresh_tick)
            self.call_later(self._schedule_refresh)

        def action_refresh(self) -> None:
            self._schedule_refresh()

        def action_toggle_auto_refresh(self) -> None:
            self._auto_refresh_enabled = not self._auto_refresh_enabled
            mode = "on" if self._auto_refresh_enabled else "off"
            self._update_auto_refresh_button()
            self.query_one("#events", RichLog).write(f"[yellow]auto-refresh[/yellow]: {mode}")

        def action_view_entry(self) -> None:
            entry = self._selected_recent_entry()
            if entry is None:
                self.query_one("#events", RichLog).write("[yellow]view[/yellow]: no recent entry selected")
                return
            self.push_screen(DetailModal(entry))

        def action_toggle_blocked(self) -> None:
            self.blocked_only = not self.blocked_only
            mode = "on" if self.blocked_only else "off"
            events = self.query_one("#events", RichLog)
            events.write(f"[yellow]blocked-only[/yellow]: {mode}")
            self._schedule_refresh()

        def action_cycle_source(self) -> None:
            entries = _entries_for_scope(
                scope=self.scope,
                log_path=self.log_path,
                max_files=self.max_files,
                max_lines_per_file=self.max_lines_per_file,
                explicit_log_path=self.explicit_log_path,
            )
            options = self._source_cycle_options(entries)
            self.source_filter = self._cycle(self.source_filter, options)
            self.query_one("#events", RichLog).write(f"[yellow]source filter[/yellow]: {self.source_filter}")
            self._schedule_refresh()

        def action_cycle_risk(self) -> None:
            options = ["all", "low", "medium", "high"]
            self.risk_filter = self._cycle(self.risk_filter, options)
            self.query_one("#events", RichLog).write(f"[yellow]risk filter[/yellow]: {self.risk_filter}")
            self._schedule_refresh()

        def action_cycle_family(self) -> None:
            options = ["all", "network", "python", "filesystem", "inspection", "other"]
            self.family_filter = self._cycle(self.family_filter, options)
            self.query_one("#events", RichLog).write(f"[yellow]family filter[/yellow]: {self.family_filter}")
            self._schedule_refresh()

        def action_cycle_domain(self) -> None:
            entries = _entries_for_scope(
                scope=self.scope,
                log_path=self.log_path,
                max_files=self.max_files,
                max_lines_per_file=self.max_lines_per_file,
                explicit_log_path=self.explicit_log_path,
            )
            options = ["all", *sorted({str(e.get("domain") or e.get("source") or "unknown") for e in entries})]
            self.domain_filter = self._cycle(self.domain_filter, options)
            self.query_one("#events", RichLog).write(f"[yellow]domain filter[/yellow]: {self.domain_filter}")
            self._schedule_refresh()

        def on_button_pressed(self, event: Any) -> None:
            if event.button.id == "refresh-now":
                self.action_refresh()
            elif event.button.id == "toggle-auto-refresh":
                self.action_toggle_auto_refresh()
            elif event.button.id == "view-entry":
                self.action_view_entry()

        def on_data_table_row_selected(self, event) -> None:
            if getattr(event.data_table, "id", None) == "recent":
                self.action_view_entry()

        def _auto_refresh_tick(self) -> None:
            if self._auto_refresh_enabled:
                self._schedule_refresh()

        def _update_auto_refresh_button(self) -> None:
            label = "Auto refresh: on" if self._auto_refresh_enabled else "Auto refresh: off"
            self.query_one("#toggle-auto-refresh", Button).label = label

        def _selected_recent_entry(self) -> dict[str, Any] | None:
            if not self._current_recent_entries:
                return None
            recent = self.query_one("#recent", DataTable)
            try:
                row_index = int(recent.cursor_row)
            except Exception:
                row_index = 0
            if row_index < 0 or row_index >= len(self._current_recent_entries):
                return None
            return self._current_recent_entries[row_index]

        def _schedule_refresh(self) -> None:
            if self._inflight:
                return
            self._inflight = True
            asyncio.create_task(self._refresh())

        async def _refresh(self) -> None:
            stats = self.query_one("#stats", Static)
            monitoring = self.query_one("#monitoring", Static)
            recent = self.query_one("#recent", DataTable)
            blocked = self.query_one("#blocked", DataTable)
            top = self.query_one("#top", DataTable)
            events = self.query_one("#events", RichLog)
            refresh_started = time.perf_counter()

            try:
                entries = _entries_for_scope(
                    scope=self.scope,
                    log_path=self.log_path,
                    max_files=self.max_files,
                    max_lines_per_file=self.max_lines_per_file,
                    explicit_log_path=self.explicit_log_path,
                )
                filtered_entries = _apply_filters(
                    entries,
                    blocked_only=self.blocked_only,
                    source_filter=self.source_filter,
                    risk_filter=self.risk_filter,
                    family_filter=self.family_filter,
                    domain_filter=self.domain_filter,
                )
                stats_summary = _collect_stats(filtered_entries)
                mode = "on" if self.blocked_only else "off"
                avg_dur = stats_summary.get("avg_duration_ms")
                avg_text = "-" if avg_dur is None else f"{avg_dur:.1f} ms"
                scope_label = "project" if self.scope == "project" else "sys"
                stats.update(
                    f"[bold cyan]Scope[/bold cyan]: {scope_label}    "
                    f"[bold cyan]Filtered[/bold cyan]: {len(filtered_entries)}/{len(entries)}    "
                    f"[bold cyan]Total[/bold cyan]: {stats_summary['total']}    "
                    f"[green]Allowed[/green]: {stats_summary['allowed']}    "
                    f"[red]Blocked[/red]: {stats_summary['blocked']}    "
                    f"[yellow]Failed[/yellow]: {stats_summary['failed_allowed']}    "
                    f"[magenta]Network[/magenta]: {stats_summary['network_calls']}    "
                    f"[bold red]High-risk[/bold red]: {stats_summary['high_risk']}    "
                    f"[bold cyan]Domains[/bold cyan]: {stats_summary['domains']}    "
                    f"[bold cyan]Avg[/bold cyan]: {avg_text}    "
                    f"[bold cyan]Blocked only[/bold cyan]: {mode}    "
                    f"[bold cyan]source[/bold cyan]: {self.source_filter}    "
                    f"[bold cyan]risk[/bold cyan]: {self.risk_filter}    "
                    f"[bold cyan]family[/bold cyan]: {self.family_filter}    "
                    f"[bold cyan]domain[/bold cyan]: {self.domain_filter}"
                )

                recent_rows = _filtered_recent(filtered_entries, self.limit)
                self._current_recent_entries = recent_rows
                recent.clear(columns=False)
                for e in recent_rows:
                    args_str = _stringify_args(e.get("args"))
                    decision = str(e.get("policy_decision") or "?")
                    exit_code = e.get("exit_code")
                    exit_str = "-" if exit_code is None else str(exit_code)
                    dur = e.get("duration_ms")
                    dur_str = "-" if dur is None else f"{dur:.1f}"
                    recent.add_row(
                        _format_ts(e.get("timestamp")),
                        _operator_signal(e),
                        "BLOCKED" if decision == "blocked" else "ok",
                        str(e.get("risk_level") or "-"),
                        str(e.get("domain") or "-"),
                        str(e.get("component") or e.get("command") or "-"),
                        str(e.get("command") or "?"),
                        _short_request_id(e),
                        _reasoning_metrics_label(e),
                        args_str,
                        exit_str,
                        dur_str,
                        str(e.get("source") or "-"),
                        str(e.get("policy_reason") or ""),
                    )

                blocked.clear(columns=False)
                reasons = _reason_counter(filtered_entries)
                if reasons:
                    for reason, count in reasons.most_common(15):
                        blocked.add_row(reason, str(count))
                else:
                    blocked.add_row("no blocked calls", "0")

                top.clear(columns=False)
                component_counter: Counter[str] = Counter(
                    str(e.get("component") or e.get("command") or "?") for e in filtered_entries
                )
                blocked_by_component: Counter[str] = Counter(
                    str(e.get("component") or e.get("command") or "?")
                    for e in filtered_entries
                    if str(e.get("policy_decision") or "").lower() == "blocked"
                )
                top_rows = []
                for component, total in component_counter.most_common(10):
                    blocked_component = blocked_by_component.get(component, 0)
                    top_rows.append((component, total, blocked_component, total - blocked_component))
                if top_rows:
                    for cmd, total_cmd, blocked_cmd, allowed_cmd in top_rows:
                        top.add_row(cmd, str(total_cmd), str(blocked_cmd), str(allowed_cmd))
                else:
                    top.add_row("-", "0", "0", "0")

                suspicious = find_suspicious_entries(filtered_entries, limit=3)
                traceability_drilldown = _traceability_drilldown(filtered_entries, max_items=3)
                if suspicious:
                    events.write("[bold yellow]suspicious audit events[/bold yellow]:")
                    for entry in suspicious:
                        events.write(
                            f"- {_format_ts(entry.get('timestamp'))} {entry.get('risk_level','?')} {entry.get('command','?')}"
                            f" source={entry.get('source') or '-'} exit={entry.get('exit_code')!s}"
                        )

                if traceability_drilldown.get("incomplete_count", 0) > 0:
                    top_source = "-"
                    top_context = "-"
                    top_memory_op = "-"
                    if traceability_drilldown.get("top_missing_sources"):
                        source_name, source_count = traceability_drilldown["top_missing_sources"][0]
                        top_source = f"{source_name} ({source_count})"
                    if traceability_drilldown.get("top_missing_contexts"):
                        context_name, context_count = traceability_drilldown["top_missing_contexts"][0]
                        top_context = f"{context_name} ({context_count})"
                    if traceability_drilldown.get("top_missing_memory_ops"):
                        operation_name, operation_count = traceability_drilldown["top_missing_memory_ops"][0]
                        top_memory_op = f"{operation_name} ({operation_count})"
                    events.write(
                        "[bold magenta]traceability gaps[/bold magenta]: "
                        f"incomplete={traceability_drilldown.get('incomplete_count', 0)} "
                        f"top_source={top_source} top_context={top_context} top_memory_op={top_memory_op}"
                    )

                self._refresh_count += 1
                self._last_error = None
                self._last_refresh_label = datetime.now().strftime("%H:%M:%S")
                self._last_refresh_duration_ms = (time.perf_counter() - refresh_started) * 1000.0
                monitoring.update(
                    _monitoring_status_line(
                        scope=self.scope,
                        total_entries=len(entries),
                        filtered_entries=len(filtered_entries),
                        suspicious_count=len(suspicious),
                        blocked_count=stats_summary["blocked"],
                        missing_request_id_count=stats_summary["missing_request_id"],
                        auto_refresh_enabled=self._auto_refresh_enabled,
                        refresh_duration_ms=self._last_refresh_duration_ms,
                        last_refresh_label=self._last_refresh_label,
                        last_error=self._last_error,
                    )
                )
                refresh_message = ""
                if self._refresh_count == 1 or suspicious:
                    refresh_message = (
                        f"[cyan]refresh[/cyan]: visible={len(filtered_entries)}/{len(entries)} blocked={stats_summary['blocked']} suspicious={len(suspicious)}"
                    )
                if _should_emit_refresh_message(self._last_refresh_message, refresh_message):
                    events.write(refresh_message)
                self._last_refresh_message = refresh_message

            except Exception as exc:
                now = datetime.now().strftime("%H:%M:%S")
                self._last_error = str(exc)
                self._last_refresh_label = now
                self._last_refresh_duration_ms = (time.perf_counter() - refresh_started) * 1000.0
                monitoring.update(
                    _monitoring_status_line(
                        scope=self.scope,
                        total_entries=0,
                        filtered_entries=0,
                        suspicious_count=0,
                        blocked_count=0,
                        missing_request_id_count=0,
                        auto_refresh_enabled=self._auto_refresh_enabled,
                        refresh_duration_ms=self._last_refresh_duration_ms,
                        last_refresh_label=self._last_refresh_label,
                        last_error=self._last_error,
                    )
                )
                events.write(f"[{now}] [red]refresh failed:[/red] {exc}")
            finally:
                self._inflight = False

    return SysAuditApp


def run_sys_audit_tui(*, log_path: Path, limit: int, blocked_only: bool, interval_seconds: float = 2.0, scope: str = "sys") -> int:
    app_cls = create_sys_audit_app(
        log_path,
        limit=limit,
        blocked_only=blocked_only,
        interval_seconds=interval_seconds,
        scope=scope,
        source_filter="all",
        risk_filter="all",
        family_filter="all",
        explicit_log_path=True,
    )
    app = app_cls()
    app.run()
    return 0


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def render_recent_table(
    console,
    entries: list[dict],
    limit: int,
    blocked_only: bool,
    *,
    source_filter: str | None = None,
    risk_filter: str | None = None,
    family_filter: str | None = None,
    domain_filter: str | None = None,
) -> None:
    from rich.table import Table
    from rich import box

    filtered = _apply_filters(
        entries,
        blocked_only=blocked_only,
        source_filter=source_filter,
        risk_filter=risk_filter,
        family_filter=family_filter,
        domain_filter=domain_filter,
    )
    recent = filtered[-limit:][::-1]  # newest first

    table = Table(
        title=f"[bold]Recent Audit Events[/bold]  [dim](last {len(recent)} of {len(filtered)} entries)[/dim]",
        box=box.ROUNDED,
        show_lines=False,
        expand=True,
    )
    table.add_column("Time", style="dim", width=10, no_wrap=True)
    table.add_column("Signal", style="yellow", width=28, overflow="fold")
    table.add_column("Decision", width=9, no_wrap=True)
    table.add_column("Risk", width=8, no_wrap=True)
    table.add_column("Domain", style="magenta", width=10, no_wrap=True)
    table.add_column("Component", style="yellow", width=14, overflow="fold")
    table.add_column("Command", style="cyan", width=12, no_wrap=True)
    table.add_column("ReqId", style="magenta", width=12, no_wrap=True)
    table.add_column("RM", style="green", width=14, overflow="fold")
    table.add_column("Args", style="blue", overflow="fold")
    table.add_column("Exit", width=5, justify="right")
    table.add_column("ms", width=7, justify="right")
    table.add_column("Source", style="magenta", width=12, overflow="fold")
    table.add_column("Reason", style="dim", overflow="fold")

    for e in recent:
        decision = e.get("policy_decision", "?")
        dec_str = "[bold red]BLOCKED[/bold red]" if decision == "blocked" else "[green]ok[/green]"
        args = e.get("args") or []
        args_str = " ".join(str(a) for a in args) if args else "[dim]-[/dim]"
        exit_code = e.get("exit_code")
        exit_str = "[dim]-[/dim]" if exit_code is None else (
            f"[red]{exit_code}[/red]" if exit_code != 0 else "[green]0[/green]"
        )
        dur = e.get("duration_ms")
        dur_str = f"{dur:.1f}" if dur is not None else "[dim]-[/dim]"
        reason = e.get("policy_reason") or ""

        table.add_row(
            _format_ts(e.get("timestamp")),
            _operator_signal(e),
            dec_str,
            str(e.get("risk_level") or "-"),
            str(e.get("domain") or e.get("source") or "-"),
            str(e.get("component") or e.get("command") or "-"),
            e.get("command", "?"),
            _short_request_id(e),
            _reasoning_metrics_label(e),
            args_str,
            exit_str,
            dur_str,
            str(e.get("source") or "-"),
            reason,
        )

    console.print(table)


def render_blocked_summary(console, entries: list[dict]) -> None:
    from rich.table import Table
    from rich import box
    from rich.panel import Panel

    blocked = [e for e in entries if e.get("policy_decision") == "blocked"]
    if not blocked:
        console.print(Panel("[dim]No blocked requests.[/dim]", title="[bold red]Blocked Calls[/bold red]", box=box.ROUNDED))
        return

    reason_counter: Counter[str] = Counter()
    cmd_counter: Counter[str] = Counter()
    for e in blocked:
        reason_counter[e.get("policy_reason") or "unknown"] += 1
        cmd_counter[e.get("command") or "?"] += 1

    table = Table(
        title=f"[bold red]Blocked Calls[/bold red]  [dim]({len(blocked)} total)[/dim]",
        box=box.ROUNDED,
        show_lines=False,
    )
    table.add_column("Reason", style="dim", overflow="fold")
    table.add_column("Count", justify="right", style="red")

    for reason, count in reason_counter.most_common(15):
        table.add_row(reason, str(count))

    console.print(table)


def render_top_commands(console, entries: list[dict]) -> None:
    from rich.table import Table
    from rich import box

    cmd_counter: Counter[str] = Counter(e.get("command", "?") for e in entries)
    if not cmd_counter:
        return

    table = Table(
        title="[bold]Top Commands[/bold]",
        box=box.SIMPLE,
        show_lines=False,
        width=45,
    )
    table.add_column("Command", style="cyan")
    table.add_column("Total", justify="right")
    table.add_column("Blocked", justify="right", style="red")
    table.add_column("Allowed", justify="right", style="green")

    blocked_by_cmd: Counter[str] = Counter(
        e.get("command", "?") for e in entries if e.get("policy_decision") == "blocked"
    )

    for cmd, total in cmd_counter.most_common(10):
        blocked = blocked_by_cmd.get(cmd, 0)
        allowed = total - blocked
        table.add_row(cmd, str(total), str(blocked), str(allowed))

    console.print(table)


def render_source_summary(console, entries: list[dict]) -> None:
    from rich.table import Table
    from rich import box

    rows = _top_sources(entries)
    if not rows:
        return

    table = Table(
        title="[bold]Top Sources[/bold]",
        box=box.SIMPLE,
        show_lines=False,
        width=34,
    )
    table.add_column("Source", style="magenta")
    table.add_column("Count", justify="right")
    for source, count in rows:
        table.add_row(source, str(count))
    console.print(table)


def render_traceability_drilldown(console, entries: list[dict]) -> None:
    from rich.table import Table
    from rich import box
    from rich.panel import Panel

    drilldown = _traceability_drilldown(entries)
    incomplete = int(drilldown.get("incomplete_count") or 0)
    if incomplete == 0:
        console.print(
            Panel(
                "[dim]No traceability gaps detected in current filtered view.[/dim]",
                title="[bold magenta]Traceability Drilldown[/bold magenta]",
                box=box.ROUNDED,
            )
        )
        return

    fields_counter: Counter[str] = drilldown.get("missing_fields") or Counter()
    fields_text = ", ".join(
        f"{field}={count}"
        for field, count in fields_counter.most_common()
    ) or "-"

    table = Table(
        title=(
            "[bold magenta]Traceability Drilldown[/bold magenta]"
            f"  [dim](incomplete entries: {incomplete})[/dim]"
        ),
        box=box.ROUNDED,
        show_lines=False,
        expand=True,
    )
    table.add_column("Cluster", style="magenta", width=18, no_wrap=True)
    table.add_column("Value", overflow="fold")
    table.add_column("Count", justify="right", style="magenta", width=8, no_wrap=True)

    for source, count in drilldown.get("top_missing_sources") or []:
        table.add_row("missing_by_source", str(source), str(count))
    for context, count in drilldown.get("top_missing_contexts") or []:
        table.add_row("missing_by_context", str(context), str(count))
    for operation, count in drilldown.get("top_missing_memory_ops") or []:
        table.add_row("missing_by_memory_op", str(operation), str(count))

    console.print(table)
    console.print(f"[dim]missing_fields:[/dim] {fields_text}")


def render_suspicious_summary(console, entries: list[dict]) -> None:
    from rich.table import Table
    from rich import box
    from rich.panel import Panel

    suspicious = find_suspicious_entries(entries, limit=8)
    if not suspicious:
        console.print(Panel("[dim]No suspicious audit events.[/dim]", title="[bold yellow]Suspicious Events[/bold yellow]", box=box.ROUNDED))
        return

    table = Table(
        title="[bold yellow]Suspicious Events[/bold yellow]",
        box=box.ROUNDED,
        show_lines=False,
        expand=True,
    )
    table.add_column("Time", width=10, no_wrap=True)
    table.add_column("Risk", width=8, no_wrap=True)
    table.add_column("Command", style="cyan", width=12, no_wrap=True)
    table.add_column("Source", style="magenta", width=14)
    table.add_column("Reason / Signal", overflow="fold")
    for entry in suspicious:
        signal = entry.get("policy_reason") or (
            f"exit={entry.get('exit_code')} stderr={entry.get('stderr_bytes')} stdout={entry.get('stdout_bytes')}"
        )
        table.add_row(
            _format_ts(entry.get("timestamp")),
            str(entry.get("risk_level") or "-"),
            str(entry.get("command") or "?"),
            str(entry.get("source") or "-"),
            str(signal),
        )
    console.print(table)


def render_stats_bar(console, entries: list[dict]) -> None:
    from rich.text import Text
    from rich.panel import Panel
    from rich import box

    summary = _collect_stats(entries)
    avg_dur = summary.get("avg_duration_ms")
    traceability_pct = None
    if summary['total']:
        traceability_pct = (summary['traceability_complete'] / summary['total']) * 100.0

    parts = [
        f"[bold]Total:[/bold] {summary['total']}",
        f"[green]Allowed:[/green] {summary['allowed']}",
        f"[red]Blocked:[/red] {summary['blocked']}",
        f"[yellow]Failed:[/yellow] {summary['failed_allowed']}",
        f"[magenta]Network:[/magenta] {summary['network_calls']}",
        f"[bold red]High-risk:[/bold red] {summary['high_risk']}",
        f"[magenta]Missing request_id:[/magenta] {summary['missing_request_id']}",
        f"[magenta]Missing source:[/magenta] {summary['missing_source']}",
    ]
    if traceability_pct is not None:
        parts.append(f"[cyan]Traceability:[/cyan] {summary['traceability_complete']}/{summary['total']} ({traceability_pct:.1f}%)")
    if avg_dur is not None:
        parts.append(f"[dim]Avg duration:[/dim] {avg_dur:.1f} ms")

    console.print(Panel("  |  ".join(parts), title="[bold]Project Audit Stats[/bold]", box=box.ROUNDED))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LIARA Admin TUI — sys-audit viewer")
    p.add_argument("--scope", choices=["project", "sys"], default="project", help="Audit scope: project-wide logs or sys-only log")
    p.add_argument("--log", default=None, help="Path to sys_audit.jsonl (default: auto-detect)")
    p.add_argument("--limit", type=int, default=30, help="Number of recent entries to show (default: 30)")
    p.add_argument("--blocked-only", action="store_true", help="Show only blocked requests in the table")
    p.add_argument("--source", default="all", help="Filter by source (e.g. orchestrator, api, unknown)")
    p.add_argument("--risk-level", default="all", help="Filter by risk level (all, low, medium, high)")
    p.add_argument("--command-family", default="all", help="Filter by command family (all, network, python, filesystem, inspection, other)")
    p.add_argument("--domain", default="all", help="Filter by domain (all, services, workers, tests, audits, demos, tex-ui, infra)")
    p.add_argument("--max-files", type=int, default=400, help="Max log files scanned in project scope")
    p.add_argument("--max-lines-per-file", type=int, default=80, help="Max tail lines loaded per file in project scope")
    p.add_argument("--follow", action="store_true", help="Live-refresh Rich snapshot mode every 2s (Ctrl+C to quit)")
    p.add_argument("--interval", type=float, default=2.0, help="Refresh interval in seconds for --follow (default: 2)")
    mode_group = p.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--textual",
        action="store_const",
        const="textual",
        dest="ui_mode",
        help="Use the interactive Textual UI",
    )
    mode_group.add_argument(
        "--snapshot",
        "--no-textual",
        action="store_const",
        const="snapshot",
        dest="ui_mode",
        help="Use Rich snapshot output (default)",
    )
    p.set_defaults(ui_mode="snapshot")
    return p.parse_args(argv)


def _entries_for_scope(
    *,
    scope: str,
    log_path: Path,
    max_files: int,
    max_lines_per_file: int,
    explicit_log_path: bool = False,
) -> list[dict[str, Any]]:
    if scope == "project":
        return _load_project_entries(
            _project_logs_root(),
            max_files=max_files,
            max_lines_per_file=max_lines_per_file,
        )
    return _load_sys_entries(log_path, explicit_log_path=explicit_log_path)


def _render_all(
    console,
    scope: str,
    log_path: Path,
    limit: int,
    blocked_only: bool,
    *,
    source_filter: str,
    risk_filter: str,
    family_filter: str,
    domain_filter: str,
    max_files: int,
    max_lines_per_file: int,
    explicit_log_path: bool = False,
) -> None:
    from rich.rule import Rule

    entries = _entries_for_scope(
        scope=scope,
        log_path=log_path,
        max_files=max_files,
        max_lines_per_file=max_lines_per_file,
        explicit_log_path=explicit_log_path,
    )
    filtered_entries = _apply_filters(
        entries,
        blocked_only=blocked_only,
        source_filter=source_filter,
        risk_filter=risk_filter,
        family_filter=family_filter,
        domain_filter=domain_filter,
    )
    console.print(
        f"[dim]Scope:[/dim] {scope}  "
        f"[dim]Filters:[/dim] blocked_only={blocked_only} source={source_filter} risk={risk_filter} family={family_filter} domain={domain_filter}"
    )
    render_stats_bar(console, filtered_entries)
    console.print()
    render_recent_table(
        console,
        entries,
        limit=limit,
        blocked_only=blocked_only,
        source_filter=source_filter,
        risk_filter=risk_filter,
        family_filter=family_filter,
        domain_filter=domain_filter,
    )
    console.print()
    render_blocked_summary(console, filtered_entries)
    console.print()
    render_top_commands(console, filtered_entries)
    console.print()
    render_source_summary(console, filtered_entries)
    console.print()
    render_traceability_drilldown(console, filtered_entries)
    console.print()
    render_suspicious_summary(console, filtered_entries)


def main(argv: Sequence[str] | None = None) -> None:
    from rich.console import Console

    args = _parse_args(argv)
    explicit_log_path = args.log is not None
    log_path = Path(args.log) if args.log else _audit_log_path()
    console = Console()

    # By default, run a one-shot Rich snapshot so normal CLI use stays predictable.
    # Textual is only used when it is explicitly requested.
    use_textual = args.ui_mode == "textual"

    if use_textual:
        app_cls = create_sys_audit_app(
            log_path,
            limit=max(1, args.limit),
            blocked_only=args.blocked_only,
            interval_seconds=args.interval,
            scope=args.scope,
            source_filter=args.source,
            risk_filter=args.risk_level,
            family_filter=args.command_family,
            domain_filter=args.domain,
            max_files=args.max_files,
            max_lines_per_file=args.max_lines_per_file,
            explicit_log_path=explicit_log_path,
        )
        app = app_cls()
        app.run()
        return

    if args.follow:
        try:
            while True:
                console.clear()
                console.rule("[bold cyan]LIARA sys-audit  (Ctrl+C to quit)[/bold cyan]")
                _render_all(
                    console,
                    args.scope,
                    log_path,
                    limit=args.limit,
                    blocked_only=args.blocked_only,
                    source_filter=args.source,
                    risk_filter=args.risk_level,
                    family_filter=args.command_family,
                    domain_filter=args.domain,
                    max_files=args.max_files,
                    max_lines_per_file=args.max_lines_per_file,
                    explicit_log_path=explicit_log_path,
                )
                time.sleep(args.interval)
        except KeyboardInterrupt:
            console.print("\n[dim]Exiting.[/dim]")
    else:
        console.rule("[bold cyan]LIARA sys-audit[/bold cyan]")
        _render_all(
            console,
            args.scope,
            log_path,
            limit=args.limit,
            blocked_only=args.blocked_only,
            source_filter=args.source,
            risk_filter=args.risk_level,
            family_filter=args.command_family,
            domain_filter=args.domain,
            max_files=args.max_files,
            max_lines_per_file=args.max_lines_per_file,
            explicit_log_path=explicit_log_path,
        )


if __name__ == "__main__":
    main()
