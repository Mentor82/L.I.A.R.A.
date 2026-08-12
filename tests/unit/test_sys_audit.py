"""Unit tests for sys_audit.py — no WSL, no I/O required."""

from __future__ import annotations

import importlib
import json
import logging
from collections.abc import Generator
from typing import Any

import pytest

from services.tools.builtin.sys_audit import (
    count_entries,
    filter_entries,
    SysAuditEntry,
    find_suspicious_entries,
    load_entries,
    log_blocked,
    log_executed,
    log_judge_pre_action,
    summarize_entries,
    _audit_logger,
)


# ---------------------------------------------------------------------------
# Helper: capture audit log output in-memory
# ---------------------------------------------------------------------------

class _JsonCollectHandler(logging.Handler):
    """Logging handler that parses each JSON log message into a list."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        super().__init__()
        self._records = records

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._records.append(json.loads(self.format(record)))
        except Exception:
            pass


@pytest.fixture()
def capture_audit() -> Generator[list[dict[str, Any]], None, None]:
    """Yield a list that accumulates parsed audit log records in real-time."""
    records: list[dict[str, Any]] = []
    handler = _JsonCollectHandler(records)
    handler.setFormatter(logging.Formatter("%(message)s"))
    original_handlers = _audit_logger.handlers[:]
    _audit_logger.handlers = [handler]
    yield records
    _audit_logger.handlers = original_handlers


# ---------------------------------------------------------------------------
# SysAuditEntry dataclass
# ---------------------------------------------------------------------------

def test_importing_sys_audit_tui_does_not_require_tool_registry(monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "wsl")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_ROOT", "/home/liara/workspace")
    monkeypatch.setenv("LIARA_WSL_DISTRO", "Debian")

    module = importlib.import_module("services.tui.sys_audit_tui")
    assert hasattr(module, "main")


def test_sys_audit_import_is_fail_soft_when_log_handler_fails(monkeypatch):
    class _RaisingFileHandler:
        def __init__(self, *args, **kwargs):
            raise PermissionError("no logfile access")

    monkeypatch.setattr(logging, "FileHandler", _RaisingFileHandler)
    import sys

    sys.modules.pop("services.tools.builtin.sys_audit", None)
    module = importlib.import_module("services.tools.builtin.sys_audit")

    assert hasattr(module, "log_blocked")
    assert module._audit_logger is not None


class TestSysAuditEntry:
    def test_to_dict_blocked(self):
        entry = SysAuditEntry(
            command="curl",
            args=["-X", "POST", "http://x.com"],
            policy_decision="blocked",
            policy_reason="POST method not allowed",
            exit_code=None,
            duration_ms=None,
            stdout_bytes=None,
            stderr_bytes=None,
        )
        d = entry.to_dict()
        assert d["command"] == "curl"
        assert d["policy_decision"] == "blocked"
        assert d["exit_code"] is None
        assert d["duration_ms"] is None

    def test_to_dict_allowed(self):
        entry = SysAuditEntry(
            command="ls",
            args=["-la"],
            policy_decision="allowed",
            policy_reason=None,
            exit_code=0,
            duration_ms=12.5,
            stdout_bytes=1024,
            stderr_bytes=0,
        )
        d = entry.to_dict()
        assert d["policy_decision"] == "allowed"
        assert d["exit_code"] == 0
        assert d["stdout_bytes"] == 1024
        assert d["command_family"] == "inspection"
        assert d["outcome_class"] == "success"
        assert d["risk_level"] == "low"
        assert d["is_network"] is False

    def test_optional_fields_default_none(self):
        entry = SysAuditEntry(
            command="ls",
            args=None,
            policy_decision="allowed",
            policy_reason=None,
            exit_code=0,
            duration_ms=5.0,
            stdout_bytes=100,
            stderr_bytes=0,
        )
        assert entry.request_id is None
        assert entry.source is None
        assert entry.context is None
        assert entry.stdin_bytes is None
        assert entry.stdin_sha256 is None
        assert entry.target_path is None
        assert entry.storage_scope is None
        assert entry.retention_hint_seconds is None
        assert entry.write_mode is None
        assert entry.session_id is None
        assert entry.run_id is None

    def test_timestamp_set_automatically(self):
        import time
        before = time.time()
        entry = SysAuditEntry(
            command="ls", args=None, policy_decision="allowed",
            policy_reason=None, exit_code=0, duration_ms=1.0,
            stdout_bytes=0, stderr_bytes=0,
        )
        after = time.time()
        assert before <= entry.timestamp <= after

    def test_to_dict_adds_network_and_risk_metadata(self):
        entry = SysAuditEntry(
            command="curl",
            args=["-X", "DELETE", "https://api.example.com/resource"],
            policy_decision="blocked",
            policy_reason="DELETE blocked",
            exit_code=None,
            duration_ms=None,
            stdout_bytes=None,
            stderr_bytes=None,
        )
        d = entry.to_dict()
        assert d["command_family"] == "network"
        assert d["http_method"] == "DELETE"
        assert d["target_host"] == "api.example.com"
        assert d["is_network"] is True
        assert d["risk_level"] == "high"
        assert d["outcome_class"] == "blocked"
        assert d["arg_fingerprint"]

    def test_venv_pip_install_is_an_audited_network_dependency_mutation(self):
        entry = SysAuditEntry(
            command="venv-pip",
            args=["install", "--no-input", "pytest"],
            policy_decision="allowed",
            policy_reason=None,
            exit_code=0,
            duration_ms=20.0,
            stdout_bytes=10,
            stderr_bytes=0,
            target_path="/home/liara/workspace/.venv",
            write_mode="venv_install",
        )

        data = entry.to_dict()

        assert data["command_family"] == "dependency"
        assert data["is_network"] is True
        assert data["is_write"] is True
        assert data["risk_level"] == "high"

    def test_venv_pip_show_is_read_only_and_does_not_imply_network(self):
        entry = SysAuditEntry(
            command="venv-pip",
            args=["show", "pytest"],
            policy_decision="allowed",
            policy_reason=None,
            exit_code=0,
            duration_ms=5.0,
            stdout_bytes=10,
            stderr_bytes=0,
        )

        data = entry.to_dict()

        assert data["command_family"] == "dependency"
        assert data["is_network"] is False
        assert data["is_write"] is False
        assert data["risk_level"] == "low"


# ---------------------------------------------------------------------------
# log_blocked
# ---------------------------------------------------------------------------

class TestLogBlocked:
    def test_returns_entry_with_blocked_decision(self, capture_audit):
        entry = log_blocked("wget", ["http://evil.com"], "wget not allowed")
        assert entry.policy_decision == "blocked"
        assert entry.exit_code is None
        assert entry.stdout_bytes is None

    def test_writes_json_line_to_log(self, capture_audit):
        log_blocked("wget", ["http://evil.com"], "wget not allowed")
        assert len(capture_audit) == 1
        assert capture_audit[0]["policy_decision"] == "blocked"
        assert capture_audit[0]["policy_reason"] == "wget not allowed"

    def test_optional_metadata_forwarded(self, capture_audit):
        log_blocked(
            "curl", ["-X", "DELETE", "http://x.com"], "DELETE blocked",
            request_id="req-001", session_id="session-1", run_id="run-1", source="chat", context="test",
        )
        assert capture_audit[0]["request_id"] == "req-001"
        assert capture_audit[0]["session_id"] == "session-1"
        assert capture_audit[0]["run_id"] == "run-1"
        assert capture_audit[0]["source"] == "chat"

    def test_args_none_serialises(self, capture_audit):
        log_blocked("rm", None, "rm not allowed")
        assert capture_audit[0]["args"] is None

    def test_stdin_metadata_hashed_not_logged_raw(self, capture_audit):
        log_blocked("tee", ["/home/liara/workspace/report.txt"], "policy blocked",
                    stdin_text="hello", target_path="/home/liara/workspace/report.txt")
        assert capture_audit[0]["stdin_bytes"] == 5
        assert capture_audit[0]["stdin_sha256"]
        assert "stdin_text" not in capture_audit[0]
        assert capture_audit[0]["target_path"] == "/home/liara/workspace/report.txt"
        assert capture_audit[0]["storage_scope"] is None


# ---------------------------------------------------------------------------
# log_executed
# ---------------------------------------------------------------------------

class TestLogExecuted:
    def test_returns_entry_with_allowed_decision(self, capture_audit):
        entry = log_executed("ls", ["-la"], exit_code=0, duration_ms=20.0,
                             stdout_bytes=512, stderr_bytes=0)
        assert entry.policy_decision == "allowed"
        assert entry.policy_reason is None

    def test_writes_json_line_to_log(self, capture_audit):
        log_executed("echo", ["hello"], exit_code=0, duration_ms=5.0,
                     stdout_bytes=6, stderr_bytes=0)
        assert len(capture_audit) == 1
        d = capture_audit[0]
        assert d["command"] == "echo"
        assert d["exit_code"] == 0
        assert d["stdout_bytes"] == 6
        assert d["stderr_bytes"] == 0

    def test_non_zero_exit_still_allowed_decision(self, capture_audit):
        entry = log_executed("ls", ["/noexist"], exit_code=1, duration_ms=8.0,
                             stdout_bytes=0, stderr_bytes=30)
        assert entry.policy_decision == "allowed"
        assert entry.exit_code == 1

    def test_duration_ms_stored(self, capture_audit):
        log_executed("pwd", None, exit_code=0, duration_ms=3.14,
                     stdout_bytes=20, stderr_bytes=0)
        assert capture_audit[0]["duration_ms"] == pytest.approx(3.14)

    def test_optional_metadata_forwarded(self, capture_audit):
        log_executed("ls", None, exit_code=0, duration_ms=1.0,
                     stdout_bytes=0, stderr_bytes=0,
                     request_id="req-xyz", session_id="session-2", run_id="run-2", source="api")
        assert capture_audit[0]["request_id"] == "req-xyz"
        assert capture_audit[0]["session_id"] == "session-2"
        assert capture_audit[0]["run_id"] == "run-2"

    def test_falls_back_to_run_and_session_ids_for_traceability(self, capture_audit):
        log_executed("ls", None, exit_code=0, duration_ms=1.0,
                     stdout_bytes=0, stderr_bytes=0,
                     session_id="session-2", run_id="run-2")
        assert capture_audit[0]["request_id"] == "run-2"
        assert capture_audit[0]["source"] == "orchestrator"
        assert capture_audit[0]["traceability_missing_fields"] == []

    def test_execution_logs_stdin_metadata(self, capture_audit):
        log_executed(
            "tee", ["/home/liara/workspace/report.txt"],
            exit_code=0, duration_ms=1.0, stdout_bytes=10, stderr_bytes=0,
            stdin_text="report", target_path="/home/liara/workspace/report.txt",
        )
        assert capture_audit[0]["stdin_bytes"] == 6
        assert capture_audit[0]["stdin_sha256"]
        assert capture_audit[0]["target_path"] == "/home/liara/workspace/report.txt"

    def test_execution_logs_scope_and_write_mode(self, capture_audit):
        log_executed(
            "tee", ["-a", "/home/liara/temp/report.txt"],
            exit_code=0, duration_ms=1.0, stdout_bytes=10, stderr_bytes=0,
            stdin_text="report", target_path="/home/liara/temp/report.txt",
            storage_scope="temp", retention_hint_seconds=86400, write_mode="append",
        )
        assert capture_audit[0]["storage_scope"] == "temp"
        assert capture_audit[0]["retention_hint_seconds"] == 86400
        assert capture_audit[0]["write_mode"] == "append"

    def test_execution_logs_governance_proposal_id(self, capture_audit):
        log_executed(
            "health",
            None,
            exit_code=0,
            duration_ms=1.0,
            stdout_bytes=10,
            stderr_bytes=0,
            request_id="req-governed",
            source="api",
            proposal_id="sys-prop-123",
        )
        assert capture_audit[0]["proposal_id"] == "sys-prop-123"


class TestJudgeAudit:
    def test_log_judge_pre_action_includes_validator_score_and_risk_flags(self, capture_audit):
        log_judge_pre_action(
            tool_name="fact_lookup_reference",
            decision="block",
            issues=["Logic error: FACT_LOOKUP response missing [KNOWLEDGE_REFERENCE]"],
            constraints={
                "validator_score": {
                    "fach": 4,
                    "code": 3,
                    "robustheit": 2,
                    "gesamt": 3.3,
                    "note_text": "befriedigend",
                    "confidence": 0.86,
                },
                "risk_flags": ["unit_mismatch", "logic_branch_dead"],
            },
            request_id="req-judge-1",
            session_id="session-judge",
            run_id="run-judge-1",
            source="orchestrator",
            context="logic_error_missing_knowledge_reference",
        )

        assert len(capture_audit) == 1
        entry = capture_audit[0]
        assert entry["command"] == "judge:fact_lookup_reference"
        assert entry["policy_decision"] == "blocked"
        assert entry["session_id"] == "session-judge"
        assert entry["run_id"] == "run-judge-1"
        assert entry["judge_score"]["fach"] == 4
        assert entry["risk_flags"] == ["unit_mismatch", "logic_branch_dead"]


class TestAuditUtilities:
    def test_summarize_entries_counts_operational_metrics(self):
        entries = [
            {
                "policy_decision": "allowed",
                "exit_code": 0,
                "duration_ms": 10.0,
                "is_network": True,
                "is_write": False,
                "risk_level": "medium",
                "source": "orchestrator",
                "context": "web_lookup",
            },
            {
                "policy_decision": "allowed",
                "exit_code": 2,
                "duration_ms": 20.0,
                "is_network": False,
                "is_write": True,
                "risk_level": "high",
                "source": "orchestrator",
                "context": "python_exec",
            },
            {
                "policy_decision": "blocked",
                "exit_code": None,
                "duration_ms": None,
                "is_network": False,
                "is_write": False,
                "risk_level": "high",
                "source": "api",
                "context": "policy",
            },
        ]
        summary = summarize_entries(entries)
        assert summary["total"] == 3
        assert summary["allowed"] == 2
        assert summary["blocked"] == 1
        assert summary["failed_allowed"] == 1
        assert summary["network_calls"] == 1
        assert summary["write_ops"] == 1
        assert summary["high_risk"] == 2
        assert summary["avg_duration_ms"] == pytest.approx(15.0)
        assert summary["top_sources"][0] == ("orchestrator", 2)

    def test_find_suspicious_entries_returns_blocked_and_error_like_events(self):
        entries = [
            {"command": "ls", "policy_decision": "allowed", "exit_code": 0, "stderr_bytes": 0, "stdout_bytes": 10, "duration_ms": 2.0, "risk_level": "low", "timestamp": 1},
            {"command": "curl", "policy_decision": "allowed", "exit_code": 0, "stderr_bytes": 0, "stdout_bytes": 60000, "duration_ms": 1500.0, "risk_level": "medium", "timestamp": 2},
            {"command": "rm", "policy_decision": "blocked", "exit_code": None, "stderr_bytes": None, "stdout_bytes": None, "duration_ms": None, "risk_level": "high", "timestamp": 3},
        ]
        suspicious = find_suspicious_entries(entries)
        assert [entry["command"] for entry in suspicious] == ["rm", "curl"]

    def test_load_entries_reads_jsonl_and_applies_limit(self, tmp_path):
        path = tmp_path / "sys_audit.jsonl"
        path.write_text(
            '{"command":"a"}\n{"command":"b"}\n{"command":"c"}\n',
            encoding="utf-8",
        )
        loaded = load_entries(path, limit=2)
        assert [entry["command"] for entry in loaded] == ["b", "c"]
        assert count_entries(path) == 3

    def test_count_entries_includes_unterminated_final_record(self, tmp_path):
        path = tmp_path / "unterminated_sys_audit.jsonl"
        path.write_bytes(b'{"command":"a"}\n{"command":"b"}')

        assert count_entries(path) == 2

    def test_load_entries_limit_parses_only_tail_records(self, tmp_path, monkeypatch):
        path = tmp_path / "large_sys_audit.jsonl"
        path.write_text(
            "".join(f'{{"command":"cmd-{index}"}}\n' for index in range(1000)),
            encoding="utf-8",
        )
        original_loads = json.loads
        parsed_lines = 0

        def counted_loads(payload, *args, **kwargs):
            nonlocal parsed_lines
            parsed_lines += 1
            return original_loads(payload, *args, **kwargs)

        monkeypatch.setattr("services.tools.builtin.sys_audit.json.loads", counted_loads)

        loaded = load_entries(path, limit=2)

        assert [entry["command"] for entry in loaded] == ["cmd-998", "cmd-999"]
        assert parsed_lines == 2

    def test_filter_entries_supports_source_risk_family_and_blocked_only(self):
        entries = [
            {
                "command": "curl",
                "args": ["https://example.com"],
                "policy_decision": "allowed",
                "source": "orchestrator",
                "risk_level": "medium",
                "command_family": "network",
                "exit_code": 0,
            },
            {
                "command": "rm",
                "args": ["-rf", "/"],
                "policy_decision": "blocked",
                "source": "api",
                "risk_level": "high",
                "command_family": "filesystem",
                "exit_code": None,
            },
        ]

        filtered = filter_entries(
            entries,
            blocked_only=True,
            source="api",
            risk_level="high",
            command_family="filesystem",
        )
        assert len(filtered) == 1
        assert filtered[0]["command"] == "rm"
