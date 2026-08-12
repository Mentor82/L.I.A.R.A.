"""
Main Textual TUI application for LIARA Admin Dashboard.
"""

import asyncio
import re
from pathlib import Path

from textual.app import ComposeResult, SystemCommand
from textual.containers import Container, Vertical, Horizontal
from textual.dom import NoScreen
from textual.widgets import (
    Header,
    Footer,
    Static,
    TabbedContent,
    TabPane,
    Button,
    LoadingIndicator,
)
from textual.screen import Screen
from textual.reactive import reactive
from datetime import datetime, timedelta
from typing import List

from .data_layer import AdminDataLayer
from .models import SessionSnapshot, SystemStatus, ThresholdConfig

# Auto-refresh interval in seconds
_REFRESH_INTERVAL_SECONDS = 5.0


class StatusBar(Static):
    """Live status bar at bottom - auto-refreshed."""

    status_text: reactive[str] = reactive("Initializing...")

    def __init__(self, data_layer: AdminDataLayer):
        super().__init__()
        self.data_layer = data_layer

    def on_mount(self) -> None:
        self.set_interval(_REFRESH_INTERVAL_SECONDS, self._refresh_status)

    async def _refresh_status(self) -> None:
        status = await asyncio.to_thread(self.data_layer.load_system_status)
        now = datetime.now().strftime("%H:%M:%S")
        sessions_known = len(self.data_layer._known_session_ids)
        last_run = status.last_run_id[:12] if status.last_run_id else "n/a"
        errors = len(status.recent_errors or [])
        info = (
            f"[{now}] "
            f"Known/loaded sessions: {sessions_known}/{status.total_sessions} | "
            f"Active: {status.active_sessions} | "
            f"Runs: {status.total_runs} | "
            f"Mode: {status.avg_control_mode or 'N/A'} | "
            f"Last run: {last_run} | "
            f"Errors: {errors} | "
            f"Refresh: {_REFRESH_INTERVAL_SECONDS:.0f}s"
        )
        self.update(info)

    def update_status(self) -> None:
        """Public method for manual refresh trigger (schedules async task)."""
        self.app.call_later(self._refresh_status)


class SessionViewerPane(TabPane):
    """Tab: Recent sessions and run history with live data."""

    BINDINGS = [
        ("j", "next_session", "Next Session"),
        ("k", "previous_session", "Previous Session"),
        ("e", "export_selected_audit", "Export Selected Audit"),
    ]

    def __init__(self):
        super().__init__("Session Viewer")
        self.data_layer = AdminDataLayer()
        self._sessions: List[SessionSnapshot] = []
        self._selected_session_index = 0

    def compose(self) -> ComposeResult:
        with Vertical():
            with Horizontal():
                yield Button("Refresh Sessions", id="btn_refresh_sessions", variant="default")
                yield Button("Prev Session", id="btn_prev_session", variant="default")
                yield Button("Next Session", id="btn_next_session", variant="default")
                yield Button("Export Selected Audit", id="btn_export_selected_audit", variant="primary")
            yield Static(id="session_list_content")
            yield Static("", id="session_export_status")
            yield Static(
                "\n[dim]Press [r] to refresh · Use Prev/Next or [j]/[k] to change the selected session · Press [e] to export.[/dim]",
                id="session_hint",
            )

    def on_mount(self) -> None:
        self.set_interval(_REFRESH_INTERVAL_SECONDS, self._refresh_sessions)

    async def _refresh_sessions(self) -> None:
        self._sessions = await asyncio.to_thread(
            self.data_layer.load_recent_sessions, 10
        )
        self._sync_selected_session_index()
        self._propagate_selected_session()
        self._render_sessions()

    def _sync_selected_session_index(self) -> None:
        if not self._sessions:
            self._selected_session_index = 0
            return
        try:
            screen = self.screen
        except NoScreen:
            screen = None
        persisted_session_id = getattr(screen, "selected_session_id", None) if screen is not None else None
        if persisted_session_id:
            for index, session in enumerate(self._sessions):
                if session.session_id == persisted_session_id:
                    self._selected_session_index = index
                    return
        self._selected_session_index = max(0, min(self._selected_session_index, len(self._sessions) - 1))

    def _selected_session(self) -> SessionSnapshot | None:
        if not self._sessions:
            return None
        self._sync_selected_session_index()
        return self._sessions[self._selected_session_index]

    def _propagate_selected_session(self) -> None:
        try:
            screen = self.screen
        except NoScreen:
            return
        if hasattr(screen, "set_selected_session_id"):
            selected = self._selected_session()
            screen.set_selected_session_id(selected.session_id if selected is not None else None)

    def _refresh_linked_audit_timeline(self) -> None:
        try:
            screen = self.screen
        except NoScreen:
            return
        if not hasattr(screen, "query"):
            return
        for pane in screen.query(AuditTimelinePane):
            self.app.call_later(pane._refresh)

    def _select_previous_session(self) -> None:
        if not self._sessions:
            self._selected_session_index = 0
            return
        self._selected_session_index = (self._selected_session_index - 1) % len(self._sessions)
        self._propagate_selected_session()

    def _select_next_session(self) -> None:
        if not self._sessions:
            self._selected_session_index = 0
            return
        self._selected_session_index = (self._selected_session_index + 1) % len(self._sessions)
        self._propagate_selected_session()

    def _build_selected_audit_output_path(self, session_id: str) -> Path:
        safe_session = re.sub(r"[^a-zA-Z0-9._-]+", "_", session_id or "session").strip("._") or "session"
        return self.data_layer.logs_dir / "audits" / "latest" / f"session_audit_{safe_session}.json"

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_refresh_sessions":
            await self._refresh_sessions()
            return

        if event.button.id == "btn_prev_session":
            self._select_previous_session()
            self._render_sessions()
            return

        if event.button.id == "btn_next_session":
            self._select_next_session()
            self._render_sessions()
            return

        if event.button.id != "btn_export_selected_audit":
            return

        await self._export_selected_session()

    def action_previous_session(self) -> None:
        """Move selection to the previous visible session."""
        self._select_previous_session()
        self._render_sessions()
        self._refresh_linked_audit_timeline()

    def action_next_session(self) -> None:
        """Move selection to the next visible session."""
        self._select_next_session()
        self._render_sessions()
        self._refresh_linked_audit_timeline()

    async def action_export_selected_audit(self) -> None:
        """Export the currently selected session using the keyboard binding."""
        await self._export_selected_session()

    async def _export_selected_session(self) -> None:
        """Export the currently selected session and update the status line."""

        status_widget = self.query_one("#session_export_status", Static)
        target_session = self._selected_session()
        if target_session is None:
            status_widget.update("[yellow]No loaded session available for export.[/yellow]")
            return

        output_path = self._build_selected_audit_output_path(target_session.session_id)
        exported = await asyncio.to_thread(
            self.data_layer.export_session_audit_summary,
            target_session.session_id,
            str(output_path),
        )
        if exported is None:
            status_widget.update(f"[red]Export failed for session {target_session.session_id}.[/red]")
            return
        status_widget.update(f"[green]Exported audit for {target_session.session_id} to {exported}[/green]")

    def _render_sessions(self) -> None:
        widget = self.query_one("#session_list_content", Static)
        if not self._sessions:
            widget.update(
                "Session History:\n\n"
                "  No sessions available.\n\n"
                "  Sessions appear here after chat activity.\n"
                "  Set LIARA_API_BASE_URL to connect to a running API.\n\n"
                "  When sessions are loaded, each will show:\n"
                "    • Session ID and creation time\n"
                "    • Run count and control mode chain\n"
                "    • Score feedback history\n"
                "    • Escalation transitions"
            )
            return

        lines = ["Session History:\n"]
        selected = self._selected_session()
        selected_index = self._selected_session_index + 1 if selected is not None else 0
        total_sessions = len(self._sessions)
        lines.append(f"  Selected session: {selected_index}/{total_sessions}\n")

        for index, snap in enumerate(self._sessions):
            mode = snap.current_control_mode or "advisory"
            escalations = snap.trend_escalation_count
            weak = snap.weak_score_count
            prefix = "▶" if index == self._selected_session_index else " "
            lines.append(
                f" {prefix} ┌ {snap.session_id[:24]}…\n"
                f" {prefix} │  Runs: {snap.run_count}  Mode: {mode}  "
                f"Escalations: {escalations}  Weak scores: {weak}\n"
                f" {prefix} │  Created: {snap.created_at.strftime('%Y-%m-%d %H:%M:%S')}\n"
            )
            if snap.runs:
                last = snap.runs[-1]
                signals = last.math_signals or {}
                retry_control = last.retry_control or {}
                judge_post = last.judge_post or {}
                probabilistic = judge_post.get("probabilistic_signals", {}) if isinstance(judge_post, dict) else {}
                lines.append(
                    f" {prefix} │  Last run: {last.run_id[:16]}  "
                    f"Outcome: {last.outcome}  "
                    f"Retries: {last.retry_count}\n"
                )
                lines.append(
                    f" {prefix} │  Control: {signals.get('control_mode_before', last.control_mode_before)}"
                    f" -> {signals.get('control_mode_after', last.control_mode_after)}"
                    f" ({signals.get('decision_delta', {}).get('direction', last.decision_delta.direction)})\n"
                )
                lines.append(
                    f" {prefix} │  Basis: {signals.get('resolution_basis', 'n/a')}"
                    f" | Resolved: {signals.get('resolved_mode', 'n/a')}"
                    f" | Action: {signals.get('resolved_action', 'n/a')}\n"
                )
                lines.append(
                    f" {prefix} │  Phase3: stability={signals.get('stability_score', 'n/a')}"
                    f" regularization={signals.get('regularization_total', 'n/a')}"
                    f" structure_path={signals.get('structure_shortest_path', 'n/a')}\n"
                )
                lines.append(
                    f" {prefix} │  Phase4: pareto={signals.get('decision_pareto_status', 'n/a')}"
                    f" dominant={signals.get('decision_dominant_objective', 'n/a')}"
                    f" reco={signals.get('decision_recommended_mode', 'n/a')}/{signals.get('decision_recommended_action', 'n/a')}\n"
                )
                lines.append(
                    f" {prefix} │  Phase5: IG={signals.get('utility_ig', 'n/a')}"
                    f" signal_conf={signals.get('signal_confidence', 'n/a')}"
                    f" posterior={signals.get('belief_posterior', 'n/a')}"
                    f" retry={retry_control.get('strategy', 'n/a')}/{retry_control.get('stop_reason', 'n/a')}\n"
                )
                lines.append(
                    f" {prefix} │  Judge: {judge_post.get('decision', 'n/a')}"
                    f" ({judge_post.get('confidence', 'n/a')})"
                    f" | prob(stability={probabilistic.get('stability_score', 'n/a')},"
                    f" IG={probabilistic.get('utility_ig', 'n/a')})\n"
                )
            lines.append(f" {prefix} └─\n")

        widget.update("\n".join(lines))


class ClosedLoopPane(TabPane):
    """Tab: Closed-loop metrics and trends."""

    def __init__(self):
        super().__init__("Closed-Loop Metrics")
        self.data_layer = AdminDataLayer()

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(id="closed_loop_content")

    def on_mount(self) -> None:
        self.set_interval(_REFRESH_INTERVAL_SECONDS, self._refresh)

    async def _refresh(self) -> None:
        sessions = await asyncio.to_thread(
            self.data_layer.load_recent_sessions, 5
        )
        widget = self.query_one("#closed_loop_content", Static)

        if not sessions:
            widget.update(
                "Closed-Loop Tracking:\n\n"
                "  No session data available.\n\n"
                "  When connected, will display:\n"
                "    • Score feedback → control mode escalation chain\n"
                "    • Session-level weak score accumulation\n"
                "    • Trend escalation history\n"
                "    • Repair vs. block decision ratios"
            )
            return

        lines = ["Closed-Loop Metrics:\n"]
        total_runs = sum(s.run_count for s in sessions)
        total_escalations = sum(s.trend_escalation_count for s in sessions)
        total_weak = sum(s.weak_score_count for s in sessions)
        total_blocked = sum(
            1 for s in sessions for r in s.runs if r.outcome == "blocked"
        )
        total_repaired = sum(
            1 for s in sessions for r in s.runs if r.outcome == "repair"
        )
        latest_runs = [s.runs[-1] for s in sessions if s.runs]
        pareto_dominated = sum(
            1
            for r in latest_runs
            if (r.math_signals or {}).get("decision_pareto_status") == "dominated"
        )
        unstable_runs = sum(
            1
            for r in latest_runs
            if (r.math_signals or {}).get("stability_is_stable") is False
        )
        low_ig_runs = sum(
            1
            for r in latest_runs
            if float((r.math_signals or {}).get("utility_ig", 0.0) or 0.0) <= 0.0
        )
        avg_signal_conf = (
            round(
                sum(float((r.math_signals or {}).get("signal_confidence", 0.0) or 0.0) for r in latest_runs)
                / len(latest_runs),
                3,
            )
            if latest_runs
            else 0.0
        )
        retry_stops_low_ig = sum(
            1
            for r in latest_runs
            if (r.retry_control or {}).get("stop_reason") == "low_information_gain"
        )
        lines.append(
            f"  Sessions tracked:  {len(sessions)}\n"
            f"  Total runs:        {total_runs}\n"
            f"  Escalations:       {total_escalations}\n"
            f"  Weak scores:       {total_weak}\n"
            f"  Blocked runs:      {total_blocked}\n"
            f"  Repaired runs:     {total_repaired}\n"
            f"  Dominated Pareto:  {pareto_dominated}\n"
            f"  Unstable latest:   {unstable_runs}\n"
            f"  Non-positive IG:   {low_ig_runs}\n"
            f"  Avg signal conf:   {avg_signal_conf}\n"
            f"  Retry stop (IG):   {retry_stops_low_ig}\n"
        )
        widget.update("\n".join(lines))


class AuditTimelinePane(TabPane):
    """Tab: Audit event timeline."""

    BINDINGS = [
        ("a", "cycle_audit_event_type", "Cycle Audit Event Type"),
        ("z", "cycle_audit_time_window", "Cycle Audit Time Window"),
    ]

    _EVENT_TYPE_FILTERS: list[tuple[str, list[str] | None]] = [
        ("all", None),
        ("control_transition", ["control_transition"]),
        ("score_feedback", ["score_feedback"]),
        ("repair", ["repair"]),
        ("block", ["block"]),
        ("threshold_change", ["threshold_change"]),
    ]

    _TIME_WINDOW_FILTERS: list[tuple[str, int | None]] = [
        ("all", None),
        ("5m", 5),
        ("15m", 15),
        ("1h", 60),
        ("4h", 240),
    ]

    def __init__(self):
        super().__init__("Audit Timeline")
        self.data_layer = AdminDataLayer()
        self._event_type_filter_index = 0
        self._time_window_filter_index = 0

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(id="audit_timeline_content")
            yield Static(
                "\n[dim]Press [a] to cycle event type · Press [z] to cycle time window.[/dim]",
                id="audit_timeline_hint",
            )

    def _current_event_type_filter(self) -> tuple[str, list[str] | None]:
        return self._EVENT_TYPE_FILTERS[self._event_type_filter_index]

    def _current_time_window_filter(self) -> tuple[str, int | None]:
        return self._TIME_WINDOW_FILTERS[self._time_window_filter_index]

    def action_cycle_audit_event_type(self) -> None:
        self._event_type_filter_index = (self._event_type_filter_index + 1) % len(self._EVENT_TYPE_FILTERS)
        self._persist_filter_defaults()
        self.app.call_later(self._refresh)

    def action_cycle_audit_time_window(self) -> None:
        self._time_window_filter_index = (self._time_window_filter_index + 1) % len(self._TIME_WINDOW_FILTERS)
        self._persist_filter_defaults()
        self.app.call_later(self._refresh)

    def _persist_filter_defaults(self) -> None:
        try:
            screen = self.screen
        except NoScreen:
            return
        if hasattr(screen, "set_audit_filter_defaults"):
            screen.set_audit_filter_defaults(
                self._event_type_filter_index,
                self._time_window_filter_index,
            )

    def on_mount(self) -> None:
        try:
            screen = self.screen
        except NoScreen:
            screen = None
        if screen is not None:
            persisted_event_index = getattr(screen, "audit_event_filter_index", None)
            persisted_time_index = getattr(screen, "audit_time_window_filter_index", None)
            if isinstance(persisted_event_index, int):
                self._event_type_filter_index = persisted_event_index % len(self._EVENT_TYPE_FILTERS)
            if isinstance(persisted_time_index, int):
                self._time_window_filter_index = persisted_time_index % len(self._TIME_WINDOW_FILTERS)
        self.set_interval(_REFRESH_INTERVAL_SECONDS, self._refresh)

    async def _refresh(self) -> None:
        selected_session_id = None
        try:
            screen = self.screen
        except NoScreen:
            screen = None
        if screen is not None and hasattr(screen, "selected_session_id"):
            selected_session_id = screen.selected_session_id
        event_type_label, event_type_filter = self._current_event_type_filter()
        time_window_label, time_window_minutes = self._current_time_window_filter()
        events = await asyncio.to_thread(self.data_layer.load_audit_events, selected_session_id, 50, event_type_filter)
        if time_window_minutes is not None:
            cutoff = datetime.now() - timedelta(minutes=time_window_minutes)
            events = [event for event in events if event.timestamp >= cutoff]
        widget = self.query_one("#audit_timeline_content", Static)

        if not events:
            widget.update(
                "Audit Events:\n\n"
                "  No audit events available.\n\n"
                f"  Event type filter: {event_type_label}\n"
                f"  Time window: {time_window_label}\n"
                f"  Active session filter: {selected_session_id or 'all sessions'}\n"
                "  Expected source: logs/services/sys_audit.jsonl\n"
                "  When present, this timeline shows the latest:\n"
                "    • Control transitions\n"
                "    • Score feedback events\n"
                "    • Repair/revise flows\n"
                "    • Blocked actions and policy decisions"
            )
            return

        lines = ["Audit Events:\n"]
        lines.append(f"  Filter: {selected_session_id or 'all sessions'}\n")
        lines.append(f"  Event type: {event_type_label} | Time window: {time_window_label}\n")
        for event in events:
            lines.append(
                f"  [{event.timestamp.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"{event.level.upper()} {event.event_type}"
            )
            lines.append(f"    Session: {event.session_id}")
            if event.run_id:
                lines.append(f"    Run: {event.run_id}")
            lines.append(f"    Message: {event.message}")

            metadata = event.metadata if isinstance(event.metadata, dict) else {}
            command = metadata.get("command")
            source = metadata.get("source")
            context = metadata.get("context")
            if command or source or context:
                lines.append(
                    f"    Details: command={command or 'n/a'} source={source or 'n/a'} context={context or 'n/a'}"
                )
            lines.append("")

        widget.update("\n".join(lines).rstrip())


class ThresholdEditorPane(TabPane):
    """Tab: Editable threshold configuration."""

    def __init__(self):
        super().__init__("Thresholds")
        self.data_layer = AdminDataLayer()

    def compose(self) -> ComposeResult:
        config = self.data_layer.load_thresholds()
        content = (
            "Threshold Configuration:\n\n"
            f"  soft_max:                {config.soft_max}\n"
            f"  hard_max:                {config.hard_max}\n"
            f"  rds_observe_threshold:   {config.rds_observe_threshold}\n"
            f"  utility_negative_threshold: {config.utility_negative_threshold}\n"
            f"  score_weak_threshold:    {config.score_weak_threshold}\n"
            f"  weak_score_escalation_count: {config.weak_score_escalation_count}\n\n"
            "Version: " + config.version + "\n"
            "Last updated: " + config.last_updated.isoformat() + "\n"
            "By: " + config.updated_by
        )
        with Vertical():
            yield Static(content)
            yield Button("Edit Thresholds", id="btn_edit_thresholds", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button presses."""
        if event.button.id == "btn_edit_thresholds":
            from .screens_threshold_editor import ThresholdEditorScreen

            self.app.push_screen(ThresholdEditorScreen())


class ConfigEditorPane(TabPane):
    """Tab: System-wide configuration editor."""

    def __init__(self):
        super().__init__("Config")

    def compose(self) -> ComposeResult:
        yield Static(
            "Configuration Editor:\n"
            "(Full implementation pending)\n\n"
            "Will enable editing:\n"
            "  • Hybrid control rules\n"
            "  • Judge decision thresholds\n"
            "  • Repair/retry strategies\n"
            "  • Tool availability policies"
        )


class AdminDashboardScreen(Screen):
    """Main dashboard screen with tabbed interface and auto-refresh."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
    ]

    def __init__(self):
        super().__init__()
        self.data_layer = AdminDataLayer()
        self.selected_session_id: str | None = None
        self.audit_event_filter_index = 0
        self.audit_time_window_filter_index = 0
        self._load_persisted_ui_state()

    def _load_persisted_ui_state(self) -> None:
        state = self.data_layer.load_admin_ui_state()
        selected_session_id = state.get("selected_session_id")
        if isinstance(selected_session_id, str) and selected_session_id.strip():
            self.selected_session_id = selected_session_id.strip()

        event_index = state.get("audit_event_filter_index")
        if isinstance(event_index, int) and event_index >= 0:
            self.audit_event_filter_index = event_index

        time_index = state.get("audit_time_window_filter_index")
        if isinstance(time_index, int) and time_index >= 0:
            self.audit_time_window_filter_index = time_index

    def _persist_ui_state(self) -> None:
        self.data_layer.save_admin_ui_state(
            {
                "selected_session_id": self.selected_session_id,
                "audit_event_filter_index": self.audit_event_filter_index,
                "audit_time_window_filter_index": self.audit_time_window_filter_index,
            }
        )

    def set_selected_session_id(self, session_id: str | None) -> None:
        """Track the session currently selected by the Session Viewer."""
        self.selected_session_id = session_id
        self._persist_ui_state()

    def set_audit_filter_defaults(self, event_filter_index: int, time_window_filter_index: int) -> None:
        """Track and persist the current Audit Timeline filter defaults."""
        self.audit_event_filter_index = max(0, int(event_filter_index))
        self.audit_time_window_filter_index = max(0, int(time_window_filter_index))
        self._persist_ui_state()

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="main_tabs"):
            yield SessionViewerPane()
            yield ClosedLoopPane()
            yield AuditTimelinePane()
            yield ThresholdEditorPane()
            yield ConfigEditorPane()
        yield StatusBar(self.data_layer)
        yield Footer()

    def on_mount(self) -> None:
        """Set initial title and layout."""
        self.title = "LIARA Admin Dashboard - Hybrid Control Monitor"
        self.sub_title = "Closed-loop session tracking and configuration"

    async def action_refresh(self) -> None:
        """Manually refresh all data panes (async, non-blocking)."""
        try:
            self.query_one(StatusBar).update_status()
            for pane in self.query(SessionViewerPane):
                await pane._refresh_sessions()
            for pane in self.query(ClosedLoopPane):
                await pane._refresh()
            for pane in self.query(AuditTimelinePane):
                await pane._refresh()
        except Exception:
            pass  # Refresh is best-effort; don't crash the TUI


class AdminTUI:
    """Main entry point for LIARA Admin TUI."""

    def run(self) -> None:
        """Start the Textual application."""
        from textual.app import App

        class LiaraAdminApp(App):
            TITLE = "LIARA Admin"
            SCREENS = {"main": AdminDashboardScreen}  # Pass the class, not instance

            def on_mount(self) -> None:
                self.push_screen("main")

        app = LiaraAdminApp()
        app.run()


if __name__ == "__main__":
    tui = AdminTUI()
    tui.run()
