"""
Unit tests for Admin TUI data models and persistence.
"""

import json
import pytest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from dataclasses import replace

from frontend.admin_tui.models import (
    ThresholdConfig,
    RunEntry,
    SessionSnapshot,
    DecisionDelta,
    ControlMode,
    ScoreFeedback,
    AuditEvent,
)
from frontend.admin_tui.data_layer import AdminDataLayer


class TestThresholdConfig:
    """Tests for ThresholdConfig model."""

    def test_threshold_config_defaults(self):
        config = ThresholdConfig()
        assert config.soft_max == 5.0
        assert config.hard_max == 8.0
        assert config.rds_observe_threshold == 3.0
        assert config.score_weak_threshold == 3.0
        assert config.weak_score_escalation_count == 2
        assert config.version == "1.0"

    def test_threshold_config_custom(self):
        config = ThresholdConfig(
            soft_max=5.5,
            hard_max=8.5,
            version="1.1",
            updated_by="test",
        )
        assert config.soft_max == 5.5
        assert config.hard_max == 8.5
        assert config.version == "1.1"
        assert config.updated_by == "test"

    def test_threshold_config_timestamp(self):
        before = datetime.now()
        config = ThresholdConfig()
        after = datetime.now()
        assert before <= config.last_updated <= after


class TestDecisionDelta:
    """Tests for DecisionDelta model."""

    def test_decision_delta_escalation(self):
        delta = DecisionDelta(
            from_mode="advisory",
            to_mode="soft",
            changed=True,
            direction="escalated",
            reasons=["score_weak", "rds_high"],
        )
        assert delta.from_mode == "advisory"
        assert delta.to_mode == "soft"
        assert delta.changed is True
        assert delta.direction == "escalated"
        assert len(delta.reasons) == 2

    def test_decision_delta_unchanged(self):
        delta = DecisionDelta(
            from_mode="advisory",
            to_mode="advisory",
            changed=False,
            direction="unchanged",
        )
        assert delta.changed is False
        assert delta.direction == "unchanged"


class TestRunEntry:
    """Tests for RunEntry model."""

    def test_run_entry_minimal(self):
        delta = DecisionDelta(
            from_mode="advisory",
            to_mode="advisory",
            changed=False,
            direction="unchanged",
        )
        run = RunEntry(
            run_id="run-1",
            session_id="session-1",
            timestamp=datetime.now(),
            control_mode_before="advisory",
            control_mode_after="advisory",
            decision_delta=delta,
        )
        assert run.run_id == "run-1"
        assert run.session_id == "session-1"
        assert run.retry_count == 0
        assert run.outcome == "unknown"

    def test_run_entry_with_feedback(self):
        delta = DecisionDelta(
            from_mode="advisory",
            to_mode="soft",
            changed=True,
            direction="escalated",
            reasons=["score_fach_low"],
        )
        feedback = ScoreFeedback(
            score_fach=2.5,
            score_code=4.0,
            score_robustheit=3.5,
            judge_decision="warn",
        )
        run = RunEntry(
            run_id="run-2",
            session_id="session-1",
            timestamp=datetime.now(),
            control_mode_before="advisory",
            control_mode_after="soft",
            decision_delta=delta,
            score_feedback=feedback,
            outcome="repair",
        )
        assert run.score_feedback is not None
        assert run.score_feedback.score_fach == 2.5
        assert run.outcome == "repair"


class TestSessionSnapshot:
    """Tests for SessionSnapshot model."""

    def test_session_snapshot_minimal(self):
        session = SessionSnapshot(
            session_id="session-1",
            created_at=datetime.now(),
        )
        assert session.session_id == "session-1"
        assert session.run_count == 0
        assert session.current_control_mode == "advisory"
        assert session.weak_score_count == 0


class TestAdminDataLayer:
    """Tests for AdminDataLayer persistence."""

    def test_data_layer_initialization(self):
        data_layer = AdminDataLayer()
        assert data_layer.repo_root.exists()
        assert data_layer.logs_dir.exists()

    def test_threshold_persistence(self, tmp_path):
        """Test saving and loading thresholds."""
        # Create data layer with temp directory
        data_layer = AdminDataLayer(repo_root=str(tmp_path))

        # Create config
        config = ThresholdConfig(
            soft_max=5.5,
            hard_max=8.5,
            version="1.1",
            updated_by="test",
        )

        # Save
        assert data_layer.save_thresholds(config) is True

        # Load
        loaded = data_layer.load_thresholds()
        assert loaded.soft_max == 5.5
        assert loaded.hard_max == 8.5
        assert loaded.version == "1.1"
        assert loaded.updated_by == "test"

    def test_threshold_load_defaults(self, tmp_path):
        """Test loading defaults when no config exists."""
        data_layer = AdminDataLayer(repo_root=str(tmp_path))
        config = data_layer.load_thresholds()

        # Should get defaults
        assert config.soft_max == 5.0
        assert config.hard_max == 8.0
        assert config.version == "1.0"

    def test_admin_ui_state_persistence(self, tmp_path):
        data_layer = AdminDataLayer(repo_root=str(tmp_path))
        state = {
            "selected_session_id": "session-persisted",
            "audit_event_filter_index": 2,
            "audit_time_window_filter_index": 3,
        }

        assert data_layer.save_admin_ui_state(state) is True
        loaded_state = data_layer.load_admin_ui_state()

        assert loaded_state == state

    def test_admin_ui_state_load_defaults_when_missing(self, tmp_path):
        data_layer = AdminDataLayer(repo_root=str(tmp_path))

        loaded_state = data_layer.load_admin_ui_state()

        assert loaded_state == {}

    def test_infer_control_mode_prefers_math_signals_over_decision_text(self, tmp_path):
        data_layer = AdminDataLayer(repo_root=str(tmp_path))

        validation_result = {
            "decision": "accept",
            "math_signals": {
                "resolved_mode": "hard",
                "control_mode": "soft",
            },
        }

        assert data_layer._infer_control_mode(validation_result) == "hard"

    def test_build_decision_delta_surfaces_policy_priority_reasons(self, tmp_path):
        data_layer = AdminDataLayer(repo_root=str(tmp_path))

        validation_result = {
            "math_signals": {
                "resolution_basis": "policy",
                "trigger_reasons": ["judge_post_block", "score_fach_critical"],
                "resolved_action": "fallback_safe_response",
            },
            "judge_post": {"decision": "block"},
            "issues": ["post judge blocked result"],
        }

        delta = data_layer._build_decision_delta("soft", "hard", validation_result)

        assert delta.direction in {"escalation", "escalated"}
        assert any(reason == "basis: policy" for reason in delta.reasons)
        assert any(reason == "trigger: judge_post_block" for reason in delta.reasons)
        assert any(reason == "action: fallback_safe_response" for reason in delta.reasons)
        assert any(reason == "judge_post: block" for reason in delta.reasons)

    def test_extract_score_feedback_accepts_prefixed_score_keys(self, tmp_path):
        data_layer = AdminDataLayer(repo_root=str(tmp_path))

        validation_result = {
            "decision": "warn",
            "math_signals": {"score_feedback_applied": True},
            "score": {
                "score_fach": 5,
                "score_code": 4,
                "score_robustheit": 3,
                "score_gesamt": 4,
            },
        }

        feedback = data_layer._extract_score_feedback(validation_result)

        assert feedback is not None
        assert feedback.score_fach == 5.0
        assert feedback.score_code == 4.0
        assert feedback.score_robustheit == 3.0
        assert feedback.score_gesamt == 4.0
        assert feedback.judge_decision == "warn"

    def test_extract_runs_prefers_explicit_math_signal_control_transition(self, tmp_path):
        data_layer = AdminDataLayer(repo_root=str(tmp_path))

        history_data = {
            "items": [
                {
                    "run_id": "run-policy",
                    "role": "assistant",
                    "created_at": "2026-04-26T12:00:00+00:00",
                    "metadata": {
                        "validation_result": {
                            "decision": "accept",
                            "issues": [],
                            "math_signals": {
                                "control_mode_before": "soft",
                                "control_mode_after": "hard",
                                "resolution_basis": "policy",
                                "trigger_reasons": ["judge_post_block"],
                            },
                            "judge_post": {
                                "decision": "block",
                            },
                            "retry_count": 1,
                        }
                    },
                }
            ]
        }

        runs = data_layer._extract_runs_from_history(history_data, session_id="session-policy")

        assert len(runs) == 1
        run = runs[0]
        assert run.control_mode_before == "soft"
        assert run.control_mode_after == "hard"
        assert run.decision_delta.direction in {"escalation", "escalated"}
        assert any(reason == "basis: policy" for reason in run.decision_delta.reasons)

    def test_extract_runs_marks_blocked_outcome_when_judge_post_blocks(self, tmp_path):
        data_layer = AdminDataLayer(repo_root=str(tmp_path))

        history_data = {
            "items": [
                {
                    "run_id": "run-judge-block",
                    "role": "assistant",
                    "created_at": "2026-04-26T12:01:00+00:00",
                    "metadata": {
                        "validation_result": {
                            "decision": "accept",
                            "issues": [],
                            "judge_post": {
                                "decision": "block",
                                "reason_code": "judge.post.blocked",
                            },
                            "math_signals": {
                                "resolution_basis": "policy",
                                "resolved_mode": "hard",
                            },
                        }
                    },
                }
            ]
        }

        runs = data_layer._extract_runs_from_history(history_data, session_id="session-judge")

        assert len(runs) == 1
        assert runs[0].outcome == "blocked"

    def test_build_session_audit_summary_counts_policy_and_outcomes(self, tmp_path):
        data_layer = AdminDataLayer(repo_root=str(tmp_path))

        snapshot = SessionSnapshot(
            session_id="session-audit",
            created_at=datetime.now(),
            run_count=2,
            current_control_mode="hard",
            weak_score_count=1,
            trend_escalation_count=1,
            runs=[
                RunEntry(
                    run_id="run-1",
                    session_id="session-audit",
                    timestamp=datetime.now(),
                    control_mode_before="advisory",
                    control_mode_after="soft",
                    decision_delta=DecisionDelta(
                        from_mode="advisory",
                        to_mode="soft",
                        changed=True,
                        direction="escalated",
                        reasons=["trigger: feedback_soft_floor"],
                    ),
                    math_signals={"resolution_basis": "feedback"},
                    judge_post={"decision": "allow"},
                    outcome="repair",
                ),
                RunEntry(
                    run_id="run-2",
                    session_id="session-audit",
                    timestamp=datetime.now(),
                    control_mode_before="soft",
                    control_mode_after="hard",
                    decision_delta=DecisionDelta(
                        from_mode="soft",
                        to_mode="hard",
                        changed=True,
                        direction="escalation",
                        reasons=["trigger: judge_post_block"],
                    ),
                    math_signals={"resolution_basis": "policy"},
                    judge_post={"decision": "block"},
                    outcome="blocked",
                ),
            ],
        )

        payload = data_layer.build_session_audit_summary(snapshot)

        assert payload["session"]["session_id"] == "session-audit"
        assert payload["summary"]["outcome_counts"]["repair"] == 1
        assert payload["summary"]["outcome_counts"]["blocked"] == 1
        assert payload["summary"]["policy_basis_runs"] == 1
        assert payload["summary"]["judge_post_block_runs"] == 1
        assert payload["summary"]["escalation_count"] == 2

    def test_export_session_audit_summary_writes_json(self, tmp_path):
        data_layer = AdminDataLayer(repo_root=str(tmp_path))

        snapshot = SessionSnapshot(
            session_id="session-export",
            created_at=datetime.now(),
            run_count=1,
            runs=[
                RunEntry(
                    run_id="run-export",
                    session_id="session-export",
                    timestamp=datetime.now(),
                    control_mode_before="soft",
                    control_mode_after="hard",
                    decision_delta=DecisionDelta(
                        from_mode="soft",
                        to_mode="hard",
                        changed=True,
                        direction="escalated",
                        reasons=["basis: policy"],
                    ),
                    math_signals={"resolution_basis": "policy"},
                    judge_post={"decision": "block"},
                    outcome="blocked",
                )
            ],
            current_control_mode="hard",
        )

        data_layer.load_session = lambda _sid: snapshot
        target_path = tmp_path / "audit" / "session_export.json"

        exported = data_layer.export_session_audit_summary("session-export", output_path=str(target_path))

        assert exported == target_path
        assert target_path.exists()

        payload = json.loads(target_path.read_text(encoding="utf-8"))
        assert payload["session"]["session_id"] == "session-export"
        assert payload["summary"]["outcome_counts"]["blocked"] == 1
        assert payload["summary"]["policy_basis_runs"] == 1

    def test_load_audit_events_reads_sys_audit_log(self, tmp_path, monkeypatch):
        # Force the HTTP-first attempt to fail so this exercises the file
        # fallback it's actually testing, regardless of whether a real API
        # happens to be reachable at the default LIARA_API_BASE_URL on the
        # machine running the test.
        import frontend.admin_tui.data_layer as data_layer_module

        monkeypatch.setattr(data_layer_module, "HTTPX_AVAILABLE", False)

        data_layer = AdminDataLayer(repo_root=str(tmp_path))
        audit_dir = tmp_path / "logs" / "services"
        audit_dir.mkdir(parents=True)
        (audit_dir / "sys_audit.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "request_id": "session-alpha",
                            "command": "curl",
                            "policy_decision": "blocked",
                            "policy_reason": "network denied",
                            "risk_level": "high",
                            "context": "judge_pre_action:block",
                            "timestamp": 1714123456.0,
                        }
                    ),
                    json.dumps(
                        {
                            "request_id": "session-beta",
                            "command": "judge:answer",
                            "policy_decision": "allowed",
                            "judge_score": {"score_gesamt": 4.0},
                            "context": "score_feedback",
                            "timestamp": 1714123460.0,
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        events = data_layer.load_audit_events(limit=10)

        assert len(events) == 2
        assert events[0].session_id == "session-beta"
        assert events[0].event_type == "score_feedback"
        assert events[0].level == "info"
        assert events[1].session_id == "session-alpha"
        assert events[1].event_type == "block"
        assert events[1].level == "critical"
        assert "Blocked curl" in events[1].message

    def test_load_audit_events_filters_by_session_and_event_type(self, tmp_path):
        data_layer = AdminDataLayer(repo_root=str(tmp_path))
        audit_dir = tmp_path / "logs" / "services"
        audit_dir.mkdir(parents=True)
        (audit_dir / "sys_audit.jsonl").write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "request_id": "session-filter",
                            "command": "curl",
                            "policy_decision": "blocked",
                            "policy_reason": "denied",
                            "timestamp": 1714123400.0,
                        }
                    ),
                    json.dumps(
                        {
                            "request_id": "session-filter",
                            "command": "judge:answer",
                            "policy_decision": "allowed",
                            "context": "score_feedback",
                            "judge_score": {"score_gesamt": 3.0},
                            "timestamp": 1714123500.0,
                        }
                    ),
                    json.dumps(
                        {
                            "request_id": "session-other",
                            "command": "bash",
                            "policy_decision": "allowed",
                            "timestamp": 1714123600.0,
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        events = data_layer.load_audit_events(
            session_id="session-filter",
            event_types=["score_feedback"],
            limit=10,
        )

        assert len(events) == 1
        assert events[0].session_id == "session-filter"
        assert events[0].event_type == "score_feedback"

    def test_load_system_status_aggregates_recent_sessions_and_errors(self, tmp_path):
        data_layer = AdminDataLayer(repo_root=str(tmp_path))
        data_layer._known_session_ids = ["session-known"]

        recent_timestamp = datetime.now(timezone.utc)
        older_timestamp = recent_timestamp - timedelta(minutes=30)

        data_layer.load_recent_sessions = lambda limit=10: [
            SessionSnapshot(
                session_id="session-a",
                created_at=recent_timestamp,
                run_count=2,
                current_control_mode="hard",
                runs=[
                    RunEntry(
                        run_id="run-newest",
                        session_id="session-a",
                        timestamp=recent_timestamp,
                        control_mode_before="soft",
                        control_mode_after="hard",
                        decision_delta=DecisionDelta(
                            from_mode="soft",
                            to_mode="hard",
                            changed=True,
                            direction="escalation",
                        ),
                    )
                ],
            ),
            SessionSnapshot(
                session_id="session-b",
                created_at=older_timestamp,
                run_count=1,
                current_control_mode="soft",
                runs=[
                    RunEntry(
                        run_id="run-older",
                        session_id="session-b",
                        timestamp=older_timestamp,
                        control_mode_before="advisory",
                        control_mode_after="soft",
                        decision_delta=DecisionDelta(
                            from_mode="advisory",
                            to_mode="soft",
                            changed=True,
                            direction="escalation",
                        ),
                    )
                ],
            ),
        ]
        data_layer.load_audit_events = lambda session_id=None, limit=50, event_types=None: [
            AuditEvent(
                event_id="evt-1",
                session_id="session-a",
                run_id="run-newest",
                timestamp=recent_timestamp,
                event_type="block",
                level="critical",
                message="Blocked curl",
            )
        ]

        status = data_layer.load_system_status()

        assert status.total_sessions == 2
        assert status.active_sessions == 1
        assert status.total_runs == 3
        assert status.last_run_id == "run-newest"
        assert status.avg_control_mode == "hard"
        assert status.recent_errors == ["Blocked curl"]


class TestThresholdValidation:
    """Tests for threshold value validation rules."""

    def test_soft_max_less_than_hard_max(self):
        """soft_max must be < hard_max."""
        # Valid
        config = ThresholdConfig(soft_max=5.0, hard_max=8.0)
        assert config.soft_max < config.hard_max

        # Invalid would be caught at edit time
        invalid = ThresholdConfig(soft_max=9.0, hard_max=8.0)
        assert invalid.soft_max >= invalid.hard_max  # Should fail validation

    def test_positive_thresholds(self):
        """Thresholds should be positive."""
        config = ThresholdConfig(soft_max=5.0, hard_max=8.0)
        assert config.soft_max > 0
        assert config.hard_max > 0

    def test_weak_score_escalation_positive(self):
        """Escalation count must be >= 1."""
        config = ThresholdConfig(weak_score_escalation_count=2)
        assert config.weak_score_escalation_count >= 1

        # Invalid
        invalid = ThresholdConfig(weak_score_escalation_count=0)
        assert invalid.weak_score_escalation_count < 1  # Should fail validation


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
