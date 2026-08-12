"""Unit tests for sys_audit_tui.py — no I/O, no Rich terminal required."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import pytest

import services.tui.sys_audit_tui as sys_audit_tui_module
from services.tui.sys_audit_tui import (
    _apply_filters,
    _discover_project_log_files,
    _entry_detail_pairs,
    _event_log_max_lines,
    _load_entries,
    _load_project_entries,
    _traceability_drilldown,
    _monitoring_status_line,
    _operator_signal,
    _should_emit_refresh_message,
    _format_ts,
    render_blocked_summary,
    render_recent_table,
    render_source_summary,
    render_traceability_drilldown,
    render_top_commands,
    render_stats_bar,
    render_suspicious_summary,
    main,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_jsonl(entries: list[dict], path: Path) -> Path:
    with path.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")
    return path


def _sample_entries() -> list[dict]:
    import time
    now = time.time()
    return [
        {"command": "curl", "args": ["-s", "https://example.com"], "policy_decision": "allowed",
         "policy_reason": None, "exit_code": 0, "duration_ms": 120.5, "stdout_bytes": 512,
            "stderr_bytes": 0, "timestamp": now - 10, "source": "orchestrator", "risk_level": "medium",
            "is_network": True, "is_write": False},
        {"command": "ls", "args": ["-la"], "policy_decision": "allowed",
         "policy_reason": None, "exit_code": 0, "duration_ms": 8.0, "stdout_bytes": 256,
            "stderr_bytes": 0, "timestamp": now - 8, "source": "shell", "risk_level": "low",
            "is_network": False, "is_write": False},
        {"command": "wget", "args": ["http://evil.com"], "policy_decision": "blocked",
         "policy_reason": "wget not in allowed commands", "exit_code": None, "duration_ms": None,
            "stdout_bytes": None, "stderr_bytes": None, "timestamp": now - 5, "source": "orchestrator",
            "risk_level": "high", "is_network": True, "is_write": False},
        {"command": "curl", "args": ["-X", "DELETE", "http://api.com"], "policy_decision": "blocked",
         "policy_reason": "DELETE method not allowed", "exit_code": None, "duration_ms": None,
            "stdout_bytes": None, "stderr_bytes": None, "timestamp": now - 3, "source": "api",
            "risk_level": "high", "is_network": True, "is_write": True},
        {"command": "echo", "args": ["hello"], "policy_decision": "allowed",
         "policy_reason": None, "exit_code": 0, "duration_ms": 3.1, "stdout_bytes": 6,
            "stderr_bytes": 0, "timestamp": now - 1, "source": "shell", "risk_level": "low",
            "is_network": False, "is_write": False},
    ]


class _NullConsole:
    """Minimal Rich console stub — records printed renderables without outputting."""
    def __init__(self) -> None:
        self.printed: list = []

    def print(self, *args, **kwargs) -> None:
        self.printed.extend(args)

    def rule(self, *args, **kwargs) -> None:
        pass

    def clear(self) -> None:
        pass


class TestEventLogControls:
    def test_event_log_max_lines_defaults_to_2000(self, monkeypatch):
        monkeypatch.delenv("LIARA_SYS_AUDIT_TUI_MAX_EVENT_LINES", raising=False)
        assert _event_log_max_lines() == 2000

    def test_event_log_max_lines_uses_valid_env_override(self, monkeypatch):
        monkeypatch.setenv("LIARA_SYS_AUDIT_TUI_MAX_EVENT_LINES", "3500")
        assert _event_log_max_lines() == 3500

    def test_event_log_max_lines_ignores_invalid_or_non_positive_values(self, monkeypatch):
        monkeypatch.setenv("LIARA_SYS_AUDIT_TUI_MAX_EVENT_LINES", "not-a-number")
        assert _event_log_max_lines() == 2000
        monkeypatch.setenv("LIARA_SYS_AUDIT_TUI_MAX_EVENT_LINES", "0")
        assert _event_log_max_lines() == 2000

    def test_should_emit_refresh_message_deduplicates_identical_messages(self):
        assert _should_emit_refresh_message(None, "refresh: visible=1/1 blocked=0 suspicious=0")
        assert not _should_emit_refresh_message("same", "same")
        assert _should_emit_refresh_message("before", "after")
        assert not _should_emit_refresh_message("before", "")

    def test_refresh_message_can_reappear_after_quiet_cycle(self):
        previous = "refresh: visible=1/1 blocked=0 suspicious=1"
        assert not _should_emit_refresh_message(previous, previous)
        assert not _should_emit_refresh_message(previous, "")
        assert _should_emit_refresh_message("", previous)


# ---------------------------------------------------------------------------
# _load_entries
# ---------------------------------------------------------------------------

class TestLoadEntries:
    def test_load_valid_jsonl(self, tmp_path):
        entries = _sample_entries()
        p = _make_jsonl(entries, tmp_path / "audit.jsonl")
        loaded = _load_entries(p)
        assert len(loaded) == len(entries)

    def test_missing_file_returns_empty(self, tmp_path):
        assert _load_entries(tmp_path / "nope.jsonl") == []

    def test_skips_invalid_json_lines(self, tmp_path):
        p = tmp_path / "audit.jsonl"
        p.write_text('{"command": "ls"}\nBAD LINE\n{"command": "echo"}\n', encoding="utf-8")
        loaded = _load_entries(p)
        assert len(loaded) == 3
        assert any(entry.get("message") == "BAD LINE" for entry in loaded)

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "audit.jsonl"
        p.write_text("", encoding="utf-8")
        assert _load_entries(p) == []


class TestProjectAuditLoaders:
    def test_discover_project_log_files_finds_nested_logs(self, tmp_path):
        (tmp_path / "services").mkdir(parents=True)
        (tmp_path / "workers").mkdir(parents=True)
        (tmp_path / "services" / "sys_audit.jsonl").write_text('{"command":"ls"}\n', encoding="utf-8")
        (tmp_path / "workers" / "llm.log").write_text("worker started\n", encoding="utf-8")

        found = _discover_project_log_files(tmp_path, max_files=10)
        names = {path.name for path in found}
        assert "sys_audit.jsonl" in names
        assert "llm.log" in names

    def test_load_project_entries_normalizes_json_and_text(self, tmp_path):
        (tmp_path / "services").mkdir(parents=True)
        (tmp_path / "audits").mkdir(parents=True)
        (tmp_path / "services" / "sys_audit.jsonl").write_text(
            '{"command":"curl","policy_decision":"blocked","policy_reason":"denied","timestamp":1000}\n',
            encoding="utf-8",
        )
        (tmp_path / "audits" / "session.log").write_text("ERROR worker crashed\n", encoding="utf-8")

        entries = _load_project_entries(tmp_path, max_files=10, max_lines_per_file=20)
        assert len(entries) >= 2
        assert any(entry.get("domain") == "services" for entry in entries)
        assert any(entry.get("domain") == "audits" for entry in entries)
        assert any(str(entry.get("risk_level")) in {"high", "medium", "low"} for entry in entries)

    def test_apply_filters_supports_domain(self):
        entries = [
            {"policy_decision": "allowed", "source": "orchestrator", "domain": "services", "risk_level": "low", "command": "ls"},
            {"policy_decision": "blocked", "source": "audit-run", "domain": "audits", "risk_level": "high", "command": "curl"},
        ]

        filtered = _apply_filters(
            entries,
            blocked_only=False,
            source_filter="all",
            risk_filter="all",
            family_filter="all",
            domain_filter="audits",
        )
        assert len(filtered) == 1
        assert filtered[0]["domain"] == "audits"

    def test_load_project_entries_expands_runtime_audit_results(self, tmp_path):
        (tmp_path / "audits").mkdir(parents=True)
        report = {
            "generated_at": 1710000000.0,
            "results": [
                {
                    "prompt": "check",
                    "metadata": {
                        "reasoning_metrics": {
                            "compute_backend": "julia",
                            "compute_path": "primary",
                            "total_cost": 2.13,
                            "rds_v2": 0.77,
                            "total_risk": 0.22,
                            "fallback_reason": None,
                        }
                    },
                }
            ],
        }
        (tmp_path / "audits" / "reasoning_metrics_runtime_audit.json").write_text(
            json.dumps(report),
            encoding="utf-8",
        )

        entries = _load_project_entries(tmp_path, max_files=10, max_lines_per_file=20)
        assert len(entries) == 1
        assert entries[0].get("reasoning_metrics_backend") == "julia"
        assert entries[0].get("reasoning_metrics_path") == "primary"
        assert entries[0].get("reasoning_metrics_total_cost") == 2.13
        assert entries[0].get("source") == "reasoning_metrics_audit"


# ---------------------------------------------------------------------------
# _format_ts
# ---------------------------------------------------------------------------

class TestFormatTs:
    def test_none_returns_dash(self):
        assert _format_ts(None) == "-"

    def test_valid_timestamp_formats(self):
        import time
        result = _format_ts(time.time())
        # should be HH:MM:SS
        assert len(result) == 8
        assert result.count(":") == 2


class TestMainDefaults:
    def test_parse_args_defaults_to_snapshot_mode(self):
        args = sys_audit_tui_module._parse_args([])

        assert args.ui_mode == "snapshot"

    def test_parse_args_textual_selects_interactive_mode(self):
        args = sys_audit_tui_module._parse_args(["--textual"])

        assert args.ui_mode == "textual"

    def test_main_uses_snapshot_mode_by_default_for_tty(self, monkeypatch):
        created = []

        class DummyConsole:
            def print(self, *args, **kwargs):
                pass

            def rule(self, *args, **kwargs):
                pass

            def clear(self):
                pass

        def fake_parse_args(argv):
            return argparse.Namespace(
                log=None,
                scope="sys",
                limit=5,
                blocked_only=False,
                source="all",
                risk_level="all",
                command_family="all",
                domain="all",
                max_files=20,
                max_lines_per_file=20,
                follow=False,
                interval=2.0,
                ui_mode="snapshot",
            )

        def fake_create_app(*args, **kwargs):
            created.append((args, kwargs))
            raise AssertionError("textual app should not be created in snapshot mode")

        def fake_render_all(*args, **kwargs):
            return None

        monkeypatch.setattr(sys_audit_tui_module, "_parse_args", fake_parse_args)
        monkeypatch.setattr(sys_audit_tui_module, "_render_all", fake_render_all)
        monkeypatch.setattr(sys_audit_tui_module, "create_sys_audit_app", fake_create_app)
        monkeypatch.setattr(sys_audit_tui_module.sys.stdout, "isatty", lambda: True)
        monkeypatch.setattr("rich.console.Console", DummyConsole)

        sys_audit_tui_module.main(None)

        assert created == []

    def test_main_follow_uses_snapshot_loop_without_textual(self, monkeypatch):
        created = []
        rendered = []

        class DummyConsole:
            def print(self, *args, **kwargs):
                pass

            def rule(self, *args, **kwargs):
                pass

            def clear(self):
                pass

        def fake_parse_args(argv):
            return argparse.Namespace(
                log=None,
                scope="sys",
                limit=5,
                blocked_only=False,
                source="all",
                risk_level="all",
                command_family="all",
                domain="all",
                max_files=20,
                max_lines_per_file=20,
                follow=True,
                interval=2.0,
                ui_mode="snapshot",
            )

        def fake_create_app(*args, **kwargs):
            created.append((args, kwargs))
            raise AssertionError("textual app should not be created for snapshot follow mode")

        def fake_render_all(*args, **kwargs):
            rendered.append((args, kwargs))

        def fake_sleep(_seconds):
            raise KeyboardInterrupt()

        monkeypatch.setattr(sys_audit_tui_module, "_parse_args", fake_parse_args)
        monkeypatch.setattr(sys_audit_tui_module, "_render_all", fake_render_all)
        monkeypatch.setattr(sys_audit_tui_module, "create_sys_audit_app", fake_create_app)
        monkeypatch.setattr(sys_audit_tui_module.time, "sleep", fake_sleep)
        monkeypatch.setattr("rich.console.Console", DummyConsole)

        sys_audit_tui_module.main(None)

        assert created == []
        assert len(rendered) == 1


class TestDetailHelpers:
    def test_entry_detail_pairs_include_raw_json_and_file(self):
        entry = {
            "timestamp": 1000,
            "policy_decision": "blocked",
            "risk_level": "high",
            "domain": "services",
            "source": "api",
            "context": "judge_pre_action:block",
            "component": "sys_audit",
            "command": "curl",
            "args": ["-I", "https://example.com"],
            "exit_code": 7,
            "duration_ms": 15.2,
            "policy_reason": "denied",
            "message": "request blocked",
            "file": "logs/services/sys_audit.jsonl",
        }

        pairs = dict(_entry_detail_pairs(entry))
        assert pairs["Command"] == "curl"
        assert pairs["File"] == "logs/services/sys_audit.jsonl"
        assert pairs["Context"] == "judge_pre_action:block"
        assert '"policy_decision": "blocked"' in pairs["Raw JSON"]

    def test_entry_detail_pairs_show_judge_score_and_risk_flags(self):
        entry = {
            "timestamp": 1000,
            "policy_decision": "blocked",
            "risk_level": "high",
            "domain": "services",
            "source": "orchestrator",
            "context": "judge_pre_action:block",
            "command": "judge:fact_lookup_reference",
            "judge_score": {
                "fach": 5,
                "code": 4,
                "robustheit": 5,
                "gesamt": 4.7,
                "note_text": "mangelhaft",
                "confidence": 0.88,
            },
            "risk_flags": ["formula_mismatch", "crash_without_try_except"],
            "judge_constraints": {"validator_decision": "block"},
        }

        pairs = dict(_entry_detail_pairs(entry))
        assert "gesamt=4.7" in pairs["Judge Score"]
        assert "note=mangelhaft" in pairs["Judge Score"]
        assert "formula_mismatch" in pairs["Risk Flags"]
        assert '"validator_decision": "block"' in pairs["Judge Constraints"]

    def test_operator_signal_recognizes_fact_lookup_reference_guard(self):
        entry = {
            "command": "judge:fact_lookup_reference",
            "context": "logic_error_missing_knowledge_reference",
            "policy_reason": "Logic error: FACT_LOOKUP response missing [KNOWLEDGE_REFERENCE]",
            "policy_decision": "blocked",
        }

        assert _operator_signal(entry) == "fact_lookup_reference_guard"

    def test_operator_signal_prefers_validator_risk_flags(self):
        entry = {
            "command": "judge:validator",
            "context": "judge_pre_action:block",
            "policy_decision": "blocked",
            "risk_flags": ["formula_mismatch"],
        }

        assert _operator_signal(entry) == "validator_formula_mismatch"

    def test_monitoring_status_line_contains_refresh_and_error_state(self):
        text = _monitoring_status_line(
            scope="project",
            total_entries=42,
            filtered_entries=7,
            suspicious_count=2,
            blocked_count=3,
            missing_request_id_count=5,
            auto_refresh_enabled=False,
            refresh_duration_ms=18.5,
            last_refresh_label="12:00:00",
            last_error="parse failed",
        )

        assert "scope=project" in text
        assert "auto-refresh=off" in text
        assert "Visible" in text
        assert "Missing request_id" in text
        assert "parse failed" in text

    def test_entry_detail_pairs_include_request_id(self):
        entry = {
            "timestamp": 1000,
            "request_id": "req-123456",
            "command": "curl",
            "policy_decision": "allowed",
        }

        pairs = dict(_entry_detail_pairs(entry))
        assert pairs["Request ID"] == "req-123456"

    def test_entry_detail_pairs_include_reasoning_metrics_fields(self):
        entry = {
            "command": "reasoning_metrics",
            "reasoning_metrics_backend": "python",
            "reasoning_metrics_path": "fallback",
            "reasoning_metrics_fallback_reason": "Julia unavailable",
            "reasoning_metrics_total_cost": 3.4,
            "reasoning_metrics_rds_v2": 0.65,
            "reasoning_metrics_total_risk": 0.31,
        }

        pairs = dict(_entry_detail_pairs(entry))
        assert "backend=python" in pairs["Reasoning Metrics"]
        assert "path=fallback" in pairs["Reasoning Metrics"]
        assert "total_cost=3.4" in pairs["Reasoning Metrics"]
        assert pairs["RM Fallback Reason"] == "Julia unavailable"

    def test_operator_signal_detects_reasoning_metrics_fallback(self):
        entry = {
            "command": "reasoning_metrics",
            "reasoning_metrics_backend": "python",
            "reasoning_metrics_path": "fallback",
        }

        assert _operator_signal(entry) == "metrics_fallback"


# ---------------------------------------------------------------------------
# render_stats_bar
# ---------------------------------------------------------------------------

class TestRenderStatsBar:
    def test_renders_without_error(self):
        console = _NullConsole()
        render_stats_bar(console, _sample_entries())
        assert len(console.printed) > 0

    def test_empty_entries(self):
        console = _NullConsole()
        render_stats_bar(console, [])
        assert len(console.printed) > 0

    def test_no_durations_omits_avg(self):
        entries = [{"command": "wget", "policy_decision": "blocked", "policy_reason": "x",
                    "exit_code": None, "duration_ms": None}]
        console = _NullConsole()
        render_stats_bar(console, entries)


# ---------------------------------------------------------------------------
# render_recent_table
# ---------------------------------------------------------------------------

class TestRenderRecentTable:
    def test_renders_without_error(self):
        console = _NullConsole()
        render_recent_table(console, _sample_entries(), limit=10, blocked_only=False)
        assert len(console.printed) > 0

    def test_blocked_only_filter(self):
        from rich.table import Table
        entries = _sample_entries()
        console = _NullConsole()
        render_recent_table(console, entries, limit=10, blocked_only=True)
        # should render a Table with only blocked entries
        tables = [p for p in console.printed if isinstance(p, Table)]
        assert len(tables) == 1
        # 2 blocked entries in sample
        assert tables[0].row_count == 2

    def test_limit_applied(self):
        from rich.table import Table
        entries = _sample_entries()
        console = _NullConsole()
        render_recent_table(console, entries, limit=2, blocked_only=False)
        tables = [p for p in console.printed if isinstance(p, Table)]
        assert tables[0].row_count == 2

    def test_empty_entries(self):
        console = _NullConsole()
        render_recent_table(console, [], limit=10, blocked_only=False)

    def test_source_filter_limits_rows(self):
        from rich.table import Table

        console = _NullConsole()
        render_recent_table(
            console,
            _sample_entries(),
            limit=10,
            blocked_only=False,
            source_filter="api",
        )
        tables = [p for p in console.printed if isinstance(p, Table)]
        assert len(tables) == 1
        assert tables[0].row_count == 1

    def test_risk_and_family_filter_limits_rows(self):
        from rich.table import Table

        console = _NullConsole()
        render_recent_table(
            console,
            _sample_entries(),
            limit=10,
            blocked_only=False,
            risk_filter="high",
            family_filter="network",
        )
        tables = [p for p in console.printed if isinstance(p, Table)]
        assert len(tables) == 1
        assert tables[0].row_count >= 1


# ---------------------------------------------------------------------------
# render_blocked_summary
# ---------------------------------------------------------------------------

class TestRenderBlockedSummary:
    def test_renders_without_error(self):
        console = _NullConsole()
        render_blocked_summary(console, _sample_entries())
        assert len(console.printed) > 0

    def test_no_blocked_prints_panel(self):
        from rich.panel import Panel
        entries = [e for e in _sample_entries() if e["policy_decision"] == "allowed"]
        console = _NullConsole()
        render_blocked_summary(console, entries)
        panels = [p for p in console.printed if isinstance(p, Panel)]
        assert len(panels) == 1

    def test_blocked_entries_counted(self):
        from rich.table import Table
        console = _NullConsole()
        render_blocked_summary(console, _sample_entries())
        tables = [p for p in console.printed if isinstance(p, Table)]
        assert len(tables) == 1


# ---------------------------------------------------------------------------
# render_top_commands
# ---------------------------------------------------------------------------

class TestRenderTopCommands:
    def test_renders_without_error(self):
        console = _NullConsole()
        render_top_commands(console, _sample_entries())
        assert len(console.printed) > 0

    def test_empty_entries_renders_nothing(self):
        console = _NullConsole()
        render_top_commands(console, [])
        assert console.printed == []

    def test_curl_counts_blocked_and_allowed_separately(self):
        from rich.table import Table
        entries = _sample_entries()  # 2 curl: 1 allowed, 1 blocked
        console = _NullConsole()
        render_top_commands(console, entries)
        tables = [p for p in console.printed if isinstance(p, Table)]
        assert len(tables) == 1


class TestRenderSourceSummary:
    def test_renders_source_table(self):
        from rich.table import Table

        console = _NullConsole()
        render_source_summary(console, _sample_entries())
        tables = [p for p in console.printed if isinstance(p, Table)]
        assert len(tables) == 1


class TestTraceabilityDrilldown:
    def test_traceability_drilldown_groups_missing_by_source_and_context(self):
        entries = [
            {
                "command": "curl",
                "policy_decision": "allowed",
                "source": "api",
                "context": "api.tools.sys.invoke",
                "request_id": "req-1",
            },
            {
                "command": "curl",
                "policy_decision": "allowed",
                "source": "unknown",
                "context": "api.tools.sys.invoke",
                "request_id": "req-2",
            },
            {
                "command": "ls",
                "policy_decision": "allowed",
                "source": "unknown",
                "context": "",
            },
        ]

        drilldown = _traceability_drilldown(entries)
        assert drilldown["incomplete_count"] == 2
        assert ("unknown", 2) in drilldown["top_missing_sources"]
        assert any(context == "unknown" for context, _count in drilldown["top_missing_contexts"])
        assert drilldown["missing_fields"]["source"] >= 2

    def test_render_traceability_drilldown_renders_table_when_gaps_present(self):
        from rich.table import Table

        entries = [
            {
                "command": "curl",
                "policy_decision": "allowed",
                "source": "unknown",
                "context": "",
            }
        ]
        console = _NullConsole()
        render_traceability_drilldown(console, entries)
        tables = [p for p in console.printed if isinstance(p, Table)]
        assert len(tables) == 1

    def test_render_traceability_drilldown_renders_panel_when_no_gaps(self):
        from rich.panel import Panel

        entries = [
            {
                "command": "curl",
                "policy_decision": "allowed",
                "source": "api",
                "context": "api.tools.sys.invoke",
                "request_id": "req-ok",
            }
        ]
        console = _NullConsole()
        render_traceability_drilldown(console, entries)
        panels = [p for p in console.printed if isinstance(p, Panel)]
        assert len(panels) == 1

    def test_traceability_drilldown_groups_missing_memory_operations(self):
        entries = [
            {
                "command": "memory",
                "args": ["staging_stage"],
                "policy_decision": "allowed",
                "source": "memory_service",
                "context": "",
                "request_id": "",
            },
            {
                "command": "memory",
                "args": ["dreaming_decide_proposal"],
                "policy_decision": "blocked",
                "source": "unknown",
                "context": "",
                "request_id": "req-2",
            },
        ]

        drilldown = _traceability_drilldown(entries)
        memory_ops = dict(drilldown.get("top_missing_memory_ops") or [])
        assert memory_ops.get("memory.staging.stage") == 1
        assert memory_ops.get("memory.dreaming.decide.proposal") == 1

    def test_render_traceability_drilldown_includes_memory_operation_cluster(self):
        from rich.table import Table

        entries = [
            {
                "command": "memory",
                "args": ["staging_stage"],
                "policy_decision": "allowed",
                "source": "memory_service",
                "context": "",
                "request_id": "",
            }
        ]
        console = _NullConsole()
        render_traceability_drilldown(console, entries)
        tables = [p for p in console.printed if isinstance(p, Table)]
        assert len(tables) == 1
        assert tables[0].row_count >= 1


class TestRenderSuspiciousSummary:
    def test_renders_suspicious_table_for_risky_entries(self):
        from rich.table import Table

        console = _NullConsole()
        render_suspicious_summary(console, _sample_entries())
        tables = [p for p in console.printed if isinstance(p, Table)]
        assert len(tables) == 1

    def test_no_suspicious_entries_prints_panel(self):
        from rich.panel import Panel

        safe_entries = [
            {
                "command": "ls",
                "args": ["-la"],
                "policy_decision": "allowed",
                "exit_code": 0,
                "duration_ms": 1.0,
                "stdout_bytes": 10,
                "stderr_bytes": 0,
                "risk_level": "low",
                "timestamp": 1,
            }
        ]
        console = _NullConsole()
        render_suspicious_summary(console, safe_entries)
        panels = [p for p in console.printed if isinstance(p, Panel)]
        assert len(panels) == 1


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

class TestMain:
    def test_main_with_explicit_log_path(self, tmp_path, capsys):
        p = _make_jsonl(_sample_entries(), tmp_path / "audit.jsonl")
        # Should not raise
        main(["--log", str(p), "--limit", "5"])

    def test_main_blocked_only(self, tmp_path, capsys):
        p = _make_jsonl(_sample_entries(), tmp_path / "audit.jsonl")
        main(["--log", str(p), "--blocked-only"])

    def test_main_missing_log_does_not_crash(self, tmp_path):
        main(["--log", str(tmp_path / "missing.jsonl")])

    def test_main_accepts_new_filter_args(self, tmp_path):
        p = _make_jsonl(_sample_entries(), tmp_path / "audit.jsonl")
        main(
            [
                "--log",
                str(p),
                "--source",
                "orchestrator",
                "--risk-level",
                "high",
                "--command-family",
                "network",
            ]
        )
