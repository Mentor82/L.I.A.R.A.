"""
Data layer for Admin TUI - loads session/run/audit data from API and file storage.
"""

import json
import logging
import os
import re
from collections import Counter
from pathlib import Path
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta
from .models import (
    RunEntry,
    SessionSnapshot,
    ThresholdConfig,
    AuditEvent,
    SystemStatus,
    DecisionDelta,
    ScoreFeedback,
)

_LOGGER = logging.getLogger("admin_tui.data_layer")

# Try to import httpx, fall back gracefully if not available
try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class AdminDataLayer:
    """Abstraction for loading/saving admin data from API and file storage."""

    def __init__(self, repo_root: str = "c:/ai/LIARA", api_base_url: Optional[str] = None):
        self.repo_root = Path(repo_root)
        self.logs_dir = self.repo_root / "logs"
        self.db_dir = self.repo_root / "db"
        self.config_dir = self.repo_root / "config" or (self.repo_root / "docs")

        # API configuration - use environment variable or default
        self.api_base_url = api_base_url or os.environ.get("LIARA_API_BASE_URL", "http://127.0.0.1:8010")
        self._http_client: Optional[httpx.Client] = None if HTTPX_AVAILABLE else None

        # In-memory cache of known session_ids (populated from sys-audit or chat traffic)
        self._known_session_ids: List[str] = []

    # ------------------------------------------------------------------
    # UI state persistence
    # ------------------------------------------------------------------

    def load_admin_ui_state(self) -> Dict[str, Any]:
        """Load persisted Admin TUI UI state (best-effort)."""
        state_file = self.config_dir / "admin_tui_state.json"
        if not state_file.exists():
            return {}
        try:
            with open(state_file, encoding="utf-8") as f:
                state = json.load(f)
            return state if isinstance(state, dict) else {}
        except Exception:
            return {}

    def save_admin_ui_state(self, state: Dict[str, Any]) -> bool:
        """Persist Admin TUI UI state (best-effort)."""
        try:
            state_file = self.config_dir / "admin_tui_state.json"
            state_file.parent.mkdir(parents=True, exist_ok=True)
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=True, indent=2)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Session discovery & registration
    # ------------------------------------------------------------------

    def register_session(self, session_id: str) -> None:
        """Register a known session ID (called by TUI after chat events)."""
        if session_id and session_id not in self._known_session_ids:
            self._known_session_ids.append(session_id)
            # Keep list bounded
            if len(self._known_session_ids) > 100:
                self._known_session_ids = self._known_session_ids[-100:]

    def _discover_session_ids(self, limit: int = 20) -> List[str]:
        """Discover recent session IDs from sys-audit summary or in-memory cache."""
        # Prefer in-memory cache populated by register_session()
        if self._known_session_ids:
            return list(dict.fromkeys(self._known_session_ids))[-limit:]  # unique, preserve order

        # Fall back to reading from sys-audit endpoint
        try:
            client = self._get_http_client()
            resp = client.get(
                f"{self.api_base_url}/admin/sys-audit/summary",
                params={"limit": 200},
                timeout=5.0,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            # Extract unique session IDs from recent audit entries if available
            session_ids: List[str] = []
            for item in data.get("summary", {}).get("recent_sessions", []):
                sid = item.get("session_id") if isinstance(item, dict) else item
                if sid and sid not in session_ids:
                    session_ids.append(sid)
            return session_ids[:limit]
        except Exception as e:
            _LOGGER.debug(f"Could not discover session IDs from sys-audit: {e}")
            return []

    # ------------------------------------------------------------------
    # Primary data loading methods
    # ------------------------------------------------------------------

    def load_recent_sessions(self, limit: int = 10) -> List[SessionSnapshot]:
        """Load recent session snapshots by discovering session IDs from sys-audit logs."""
        if not HTTPX_AVAILABLE:
            _LOGGER.warning("httpx not available; returning empty sessions")
            return []

        try:
            session_ids = self._discover_session_ids(limit=limit)
            if not session_ids:
                return []

            sessions: List[SessionSnapshot] = []
            for sid in session_ids[:limit]:
                snap = self.load_session(sid)
                if snap is not None:
                    sessions.append(snap)
            return sessions
        except Exception as e:
            _LOGGER.error(f"Error loading recent sessions: {e}")
            return []

    def load_session(self, session_id: str) -> Optional[SessionSnapshot]:
        """Load specific session with full run history from API."""
        if not HTTPX_AVAILABLE:
            _LOGGER.warning("httpx not available; cannot load session")
            return None

        try:
            # Query API for session data
            client = self._get_http_client()
            response = client.get(
                f"{self.api_base_url}/session",
                params={
                    "session_id": session_id,
                    "user_id": "admin",  # Default user for dashboard queries
                },
                timeout=5.0,
            )
            response.raise_for_status()
            session_data = response.json()

            # Extract run history from session
            history_response = client.get(
                f"{self.api_base_url}/history",
                params={
                    "session_id": session_id,
                    "limit": 500,
                    "include_tool_messages": True,
                },
                timeout=5.0,
            )
            history_data = history_response.json() if history_response.status_code == 200 else {}

            # Parse runs from history
            runs = self._extract_runs_from_history(history_data, session_id)

            # Derive current control mode from last run
            current_mode = runs[-1].control_mode_after if runs else "unknown"

            # Build SessionSnapshot
            return SessionSnapshot(
                session_id=session_id,
                created_at=datetime.fromisoformat(
                    session_data.get("updated_at", datetime.now().isoformat())
                ),
                run_count=len(runs),
                runs=runs,
                current_control_mode=current_mode,
                weak_score_count=sum(
                    1 for r in runs if r.score_feedback is not None
                ),
                trend_escalation_count=sum(
                    1 for r in runs
                    if r.decision_delta and r.decision_delta.direction in {"escalated", "escalation"}
                ),
            )
        except Exception as e:
            _LOGGER.error(f"Error loading session {session_id}: {e}")
            return None

    def load_run(self, run_id: str) -> Optional[RunEntry]:
        """Load specific run details from API or logs."""
        # TODO: Implement when API exposes run endpoint
        return None

    def load_audit_events(
        self,
        session_id: Optional[str] = None,
        limit: int = 100,
        event_types: Optional[List[str]] = None,
    ) -> List[AuditEvent]:
        """Load audit events, optionally filtered by session or type."""
        try:
            from services.tools.builtin.sys_audit import load_entries as load_sys_audit_entries

            raw_entries = load_sys_audit_entries(self.logs_dir / "services" / "sys_audit.jsonl")
            if not raw_entries:
                return []

            allowed_types = {
                str(event_type).strip().lower()
                for event_type in (event_types or [])
                if str(event_type).strip()
            }
            requested_session = str(session_id or "").strip()

            events: List[AuditEvent] = []
            for entry in reversed(raw_entries):
                audit_event = self._to_audit_event(entry)
                if requested_session and audit_event.session_id != requested_session:
                    continue
                if allowed_types and audit_event.event_type.lower() not in allowed_types:
                    continue
                events.append(audit_event)
                if limit > 0 and len(events) >= limit:
                    break

            return events
        except Exception as e:
            _LOGGER.error(f"Error loading audit events: {e}")
            return []

    # ------------------------------------------------------------------
    # HTTP client
    # ------------------------------------------------------------------

    def _get_http_client(self) -> "httpx.Client":
        """Get or create HTTP client for API calls."""
        if not HTTPX_AVAILABLE:
            raise RuntimeError("httpx is required for API calls")

        if self._http_client is None:
            self._http_client = httpx.Client(timeout=10.0)
        return self._http_client

    # ------------------------------------------------------------------
    # Internal extraction helpers
    # ------------------------------------------------------------------

    def _extract_runs_from_history(self, history_data: Dict[str, Any], session_id: str) -> List[RunEntry]:
        """Extract RunEntry objects from memory history response with enhanced decision tracking."""
        runs: List[RunEntry] = []
        previous_decision: Optional[Dict[str, Any]] = None
        seen_run_ids: set = set()

        try:
            items = history_data.get("items", [])
            for item in items:
                # run_id is a top-level field on MemoryMessageRecord
                run_id = item.get("run_id") or item.get("metadata", {}).get("run_id")
                if not run_id or item.get("role") != "assistant":
                    continue
                if run_id in seen_run_ids:
                    continue
                seen_run_ids.add(run_id)

                metadata = item.get("metadata", {})
                validation_result = metadata.get("validation_result", {})
                math_signals = validation_result.get("math_signals", {})
                if not isinstance(math_signals, dict):
                    math_signals = {}

                # Parse created_at timestamp if available
                created_at_str = item.get("created_at")
                try:
                    timestamp = datetime.fromisoformat(created_at_str) if created_at_str else datetime.now()
                except (ValueError, TypeError):
                    timestamp = datetime.now()

                # Extract control mode transition
                explicit_before = str(math_signals.get("control_mode_before") or "").strip().lower()
                explicit_after = str(math_signals.get("control_mode_after") or "").strip().lower()
                valid_modes = {"advisory", "soft", "hard"}

                control_mode_before = (
                    explicit_before
                    if explicit_before in valid_modes
                    else (self._infer_control_mode(previous_decision) if previous_decision else "unknown")
                )
                control_mode_after = (
                    explicit_after
                    if explicit_after in valid_modes
                    else self._infer_control_mode(validation_result)
                )

                # Build decision_delta with reasoning
                decision_delta = self._build_decision_delta(
                    control_mode_before,
                    control_mode_after,
                    validation_result,
                )

                # Extract score feedback if present
                score_feedback = self._extract_score_feedback(validation_result)

                run = RunEntry(
                    run_id=run_id,
                    session_id=session_id,
                    timestamp=timestamp,
                    control_mode_before=control_mode_before,
                    control_mode_after=control_mode_after,
                    decision_delta=decision_delta,
                    math_signals=validation_result.get("math_signals", {}),
                    decision_context=validation_result.get("decision_context", {}),
                    retry_control=validation_result.get("retry_control", {}),
                    judge_post=validation_result.get("judge_post", {}),
                    score_feedback=score_feedback,
                    retry_count=validation_result.get("retry_count", 0),
                    outcome=self._infer_outcome_from_validation(validation_result),
                )
                runs.append(run)
                previous_decision = validation_result
        except Exception as e:
            _LOGGER.error(f"Error extracting runs from history: {e}")

        return runs

    def _to_audit_event(self, entry: Dict[str, Any]) -> AuditEvent:
        """Normalize one sys-audit entry into Admin TUI AuditEvent."""
        event_type = self._infer_audit_event_type(entry)
        return AuditEvent(
            event_id=str(entry.get("request_id") or entry.get("id") or self._build_audit_event_id(entry)),
            session_id=self._extract_audit_session_id(entry),
            run_id=self._extract_audit_run_id(entry),
            timestamp=self._parse_audit_timestamp(entry.get("timestamp")),
            event_type=event_type,
            level=self._infer_audit_level(entry),
            message=self._build_audit_message(entry, event_type=event_type),
            metadata=dict(entry),
        )

    def _parse_audit_timestamp(self, raw_timestamp: Any) -> datetime:
        """Parse audit timestamp from float epoch or ISO string."""
        if isinstance(raw_timestamp, (int, float)):
            try:
                return datetime.fromtimestamp(float(raw_timestamp), tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                return datetime.now(timezone.utc)

        if isinstance(raw_timestamp, str) and raw_timestamp.strip():
            normalized = raw_timestamp.strip().replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(normalized)
            except ValueError:
                return datetime.now(timezone.utc)

        return datetime.now(timezone.utc)

    def _infer_audit_event_type(self, entry: Dict[str, Any]) -> str:
        """Infer coarse AuditEvent type from sys-audit entry fields."""
        context = str(entry.get("context") or "").strip().lower()
        command = str(entry.get("command") or "").strip().lower()
        reason = str(entry.get("policy_reason") or "").strip().lower()
        decision = str(entry.get("policy_decision") or "").strip().lower()

        if "threshold" in context or "threshold" in reason:
            return "threshold_change"
        if context.startswith("judge_pre_action") or command.startswith("judge:"):
            if decision == "blocked":
                return "block"
            if "revise" in context or "revise" in reason:
                return "repair"
        if decision == "blocked":
            return "block"
        if "score" in context or entry.get("judge_score"):
            return "score_feedback"
        if "repair" in context or "revise" in reason:
            return "repair"
        return "control_transition"

    def _infer_audit_level(self, entry: Dict[str, Any]) -> str:
        """Infer severity level from policy and risk fields."""
        decision = str(entry.get("policy_decision") or "").strip().lower()
        risk_level = str(entry.get("risk_level") or "").strip().lower()
        exit_code = entry.get("exit_code")

        if decision == "blocked":
            return "critical" if risk_level == "high" else "warning"
        if isinstance(exit_code, int) and exit_code != 0:
            return "error"
        if risk_level == "high":
            return "warning"
        return "info"

    def _extract_audit_session_id(self, entry: Dict[str, Any]) -> str:
        """Extract session id from explicit or embedded audit metadata."""
        for candidate in (
            entry.get("session_id"),
            (entry.get("metadata") or {}).get("session_id") if isinstance(entry.get("metadata"), dict) else None,
            (entry.get("judge_constraints") or {}).get("session_id") if isinstance(entry.get("judge_constraints"), dict) else None,
        ):
            if candidate:
                return str(candidate)

        context = str(entry.get("context") or "")
        match = re.search(r"session[_-]?id[:=]\s*([A-Za-z0-9._:-]+)", context, re.IGNORECASE)
        if match:
            return match.group(1)

        request_id = str(entry.get("request_id") or "").strip()
        if request_id:
            return request_id
        return "system"

    def _extract_audit_run_id(self, entry: Dict[str, Any]) -> Optional[str]:
        """Extract run id from explicit or embedded audit metadata."""
        for candidate in (
            entry.get("run_id"),
            (entry.get("metadata") or {}).get("run_id") if isinstance(entry.get("metadata"), dict) else None,
            (entry.get("judge_constraints") or {}).get("run_id") if isinstance(entry.get("judge_constraints"), dict) else None,
        ):
            if candidate:
                return str(candidate)

        context = str(entry.get("context") or "")
        match = re.search(r"run[_-]?id[:=]\s*([A-Za-z0-9._:-]+)", context, re.IGNORECASE)
        return match.group(1) if match else None

    def _build_audit_message(self, entry: Dict[str, Any], *, event_type: str) -> str:
        """Build operator-facing audit message."""
        message = str(entry.get("message") or "").strip()
        if message:
            return message

        command = str(entry.get("command") or "unknown").strip()
        decision = str(entry.get("policy_decision") or "unknown").strip().lower()
        reason = str(entry.get("policy_reason") or "").strip()

        if decision == "blocked":
            return f"Blocked {command}: {reason or 'policy denied execution'}"
        if event_type == "score_feedback":
            return f"Score feedback recorded for {command}"
        if event_type == "repair":
            return f"Repair or revise flow triggered for {command}"
        return f"{command} {decision or 'executed'}"

    def _build_audit_event_id(self, entry: Dict[str, Any]) -> str:
        """Build stable fallback event id when no request id exists."""
        command = str(entry.get("command") or "unknown").strip().lower()
        timestamp = str(entry.get("timestamp") or "0").strip()
        return f"{command}:{timestamp}"

    def _infer_control_mode(self, validation_result: Optional[Dict[str, Any]]) -> str:
        """Infer control mode from validation result and math signals."""
        if not validation_result:
            return "unknown"

        math_signals = validation_result.get("math_signals", {})
        if isinstance(math_signals, dict):
            for key in ("control_mode_after", "resolved_mode", "control_mode"):
                value = str(math_signals.get(key) or "").strip().lower()
                if value in {"advisory", "soft", "hard"}:
                    return value

        decision = validation_result.get("decision", "accept")

        if decision == "block":
            return "hard"
        elif decision == "revise":
            return "soft"

        if math_signals.get("should_hard_block"):
            return "hard"
        elif math_signals.get("should_soft_limit"):
            return "soft"

        return "advisory"

    def _build_decision_delta(
        self,
        control_mode_before: str,
        control_mode_after: str,
        validation_result: Dict[str, Any],
    ) -> DecisionDelta:
        """Build complete decision_delta with transition direction and reasons."""
        changed = control_mode_before != control_mode_after

        if changed:
            direction = "escalation" if self._is_escalation(control_mode_before, control_mode_after) else "deescalation"
        else:
            direction = "unchanged"

        reasons: List[str] = []

        math_signals = validation_result.get("math_signals", {})
        if isinstance(math_signals, dict):
            resolution_basis = str(math_signals.get("resolution_basis") or "").strip()
            if resolution_basis:
                reasons.append(f"basis: {resolution_basis}")

            for trigger in list(math_signals.get("trigger_reasons") or [])[:3]:
                if trigger:
                    reasons.append(f"trigger: {trigger}")

            resolved_action = str(math_signals.get("resolved_action") or "").strip()
            if resolved_action:
                reasons.append(f"action: {resolved_action}")

        judge_post = validation_result.get("judge_post", {})
        if isinstance(judge_post, dict):
            judge_decision = str(judge_post.get("decision") or "").strip().lower()
            if judge_decision in {"warn", "revise", "block"}:
                reasons.append(f"judge_post: {judge_decision}")

        issues = validation_result.get("issues", [])
        if issues:
            reasons.extend(issues[:3])

        decision_context = validation_result.get("decision_context", {})
        if decision_context.get("trigger"):
            reasons.insert(0, f"trigger: {decision_context['trigger']}")

        gap_detection = validation_result.get("gap_detection", {})
        if gap_detection.get("gap_detected"):
            reasons.append(f"gap: {gap_detection.get('gap_type', 'unknown')}")

        return DecisionDelta(
            from_mode=control_mode_before,
            to_mode=control_mode_after,
            changed=changed,
            direction=direction,
            reasons=reasons[:5],
        )

    def _is_escalation(self, mode_before: str, mode_after: str) -> bool:
        """Check if transition is an escalation."""
        escalation_order = {"advisory": 0, "soft": 1, "hard": 2, "unknown": -1}
        before_rank = escalation_order.get(mode_before, -1)
        after_rank = escalation_order.get(mode_after, -1)
        return after_rank > before_rank

    def _extract_score_feedback(self, validation_result: Dict[str, Any]) -> Optional[ScoreFeedback]:
        """Extract score feedback from validation result if present."""
        math_signals = validation_result.get("math_signals", {})

        if not math_signals.get("score_feedback_applied"):
            return None

        score = validation_result.get("score", {})
        if not score:
            return None

        try:
            fach = score.get("score_fach", score.get("fach"))
            code = score.get("score_code", score.get("code"))
            robustheit = score.get("score_robustheit", score.get("robustheit"))
            gesamt = score.get("score_gesamt", score.get("gesamt"))
            judge_decision = validation_result.get("decision", "accept")
            return ScoreFeedback(
                score_fach=float(fach) if fach is not None else None,
                score_code=float(code) if code is not None else None,
                score_robustheit=float(robustheit) if robustheit is not None else None,
                score_gesamt=float(gesamt) if gesamt is not None else None,
                judge_decision=str(judge_decision),
            )
        except Exception as e:
            _LOGGER.debug(f"Could not extract score feedback: {e}")
            return None

    def _infer_outcome_from_validation(self, validation_result: Dict[str, Any]) -> str:
        """Infer run outcome from validation result."""
        judge_post = validation_result.get("judge_post", {})
        if isinstance(judge_post, dict):
            judge_decision = str(judge_post.get("decision") or "").strip().lower()
            if judge_decision == "block":
                return "blocked"
            if judge_decision in {"warn", "revise"}:
                return "repair"

        decision = validation_result.get("decision", "accept")
        if decision == "block":
            return "blocked"
        elif decision == "revise":
            return "repair"
        elif decision == "accept":
            return "success"
        return "unknown"

    # ------------------------------------------------------------------
    # Threshold configuration
    # ------------------------------------------------------------------

    def load_thresholds(self) -> ThresholdConfig:
        """Load current threshold configuration."""
        config_file = self.config_dir / "thresholds.json"
        if config_file.exists():
            try:
                with open(config_file) as f:
                    data = json.load(f)
                if isinstance(data.get("last_updated"), str):
                    data["last_updated"] = datetime.fromisoformat(data["last_updated"])
                return ThresholdConfig(**data)
            except Exception:
                pass
        return ThresholdConfig()

    def save_thresholds(self, config: ThresholdConfig) -> bool:
        """Save updated threshold configuration."""
        try:
            config_file = self.config_dir / "thresholds.json"
            config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(config_file, "w") as f:
                json.dump(
                    {
                        "soft_max": config.soft_max,
                        "hard_max": config.hard_max,
                        "rds_observe_threshold": config.rds_observe_threshold,
                        "utility_negative_threshold": config.utility_negative_threshold,
                        "score_weak_threshold": config.score_weak_threshold,
                        "weak_score_escalation_count": config.weak_score_escalation_count,
                        "version": config.version,
                        "last_updated": config.last_updated.isoformat(),
                        "updated_by": config.updated_by,
                    },
                    f,
                    indent=2,
                )
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # System status
    # ------------------------------------------------------------------

    def load_system_status(self) -> SystemStatus:
        """Load overall system status snapshot."""
        thresholds = self.load_thresholds()
        sessions = self.load_recent_sessions(limit=10)
        events = self.load_audit_events(limit=50)

        now_utc = datetime.now(timezone.utc)
        total_sessions = max(len(self._known_session_ids), len(sessions))
        total_runs = sum(int(session.run_count or 0) for session in sessions)
        mode_counts: Counter[str] = Counter()
        latest_run_id: Optional[str] = None
        latest_run_timestamp: Optional[datetime] = None
        active_sessions = 0

        for session in sessions:
            if session.current_control_mode:
                mode_counts[str(session.current_control_mode)] += 1

            reference_timestamp = session.runs[-1].timestamp if session.runs else session.created_at
            if reference_timestamp.tzinfo is None:
                reference_timestamp = reference_timestamp.replace(tzinfo=timezone.utc)

            if latest_run_timestamp is None or reference_timestamp > latest_run_timestamp:
                latest_run_timestamp = reference_timestamp
                latest_run_id = session.runs[-1].run_id if session.runs else None

            if reference_timestamp >= now_utc - timedelta(minutes=15):
                active_sessions += 1

        recent_errors = [
            str(event.message)
            for event in events
            if str(event.level).lower() in {"error", "critical"}
        ][:5]

        return SystemStatus(
            timestamp=datetime.now(),
            total_sessions=total_sessions,
            active_sessions=active_sessions,
            total_runs=total_runs,
            last_run_id=latest_run_id,
            last_run_timestamp=latest_run_timestamp,
            avg_control_mode=mode_counts.most_common(1)[0][0] if mode_counts else None,
            recent_errors=recent_errors,
            thresholds=thresholds,
        )

    def write_audit_event(self, event: AuditEvent) -> bool:
        """Write audit event to log."""
        # TODO: integrate with audit logger
        return True

    def build_session_audit_summary(self, snapshot: SessionSnapshot) -> Dict[str, Any]:
        """Build deterministic audit summary payload for a session snapshot."""
        runs = list(snapshot.runs or [])
        outcome_counts: Counter[str] = Counter()
        control_mode_after_counts: Counter[str] = Counter()
        resolution_basis_counts: Counter[str] = Counter()

        escalation_count = 0
        policy_basis_runs = 0
        judge_post_block_runs = 0

        for run in runs:
            outcome = str(run.outcome or "unknown")
            outcome_counts[outcome] += 1

            mode_after = str(run.control_mode_after or "unknown")
            control_mode_after_counts[mode_after] += 1

            if str(getattr(run.decision_delta, "direction", "")).lower() in {"escalation", "escalated"}:
                escalation_count += 1

            math_signals = run.math_signals if isinstance(run.math_signals, dict) else {}
            resolution_basis = str(math_signals.get("resolution_basis") or "unknown")
            resolution_basis_counts[resolution_basis] += 1
            if resolution_basis == "policy":
                policy_basis_runs += 1

            judge_post = run.judge_post if isinstance(run.judge_post, dict) else {}
            if str(judge_post.get("decision") or "").lower() == "block":
                judge_post_block_runs += 1

        return {
            "audit": "admin_tui_session_summary",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "session": {
                "session_id": snapshot.session_id,
                "run_count": int(snapshot.run_count),
                "current_control_mode": str(snapshot.current_control_mode or "unknown"),
                "weak_score_count": int(snapshot.weak_score_count),
                "trend_escalation_count": int(snapshot.trend_escalation_count),
            },
            "summary": {
                "outcome_counts": dict(outcome_counts),
                "control_mode_after_counts": dict(control_mode_after_counts),
                "resolution_basis_counts": dict(resolution_basis_counts),
                "escalation_count": escalation_count,
                "policy_basis_runs": policy_basis_runs,
                "judge_post_block_runs": judge_post_block_runs,
            },
        }

    def export_session_audit_summary(self, session_id: str, output_path: Optional[str] = None) -> Optional[Path]:
        """Export one session summary to JSON and return the written file path."""
        snapshot = self.load_session(session_id)
        if snapshot is None:
            return None

        payload = self.build_session_audit_summary(snapshot)

        if output_path:
            target = Path(output_path)
        else:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            audit_dir = self.logs_dir / "audits" / stamp
            safe_session = re.sub(r"[^a-zA-Z0-9._-]+", "_", snapshot.session_id or "session").strip("._") or "session"
            target = audit_dir / f"session_audit_{safe_session}.json"

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        return target
