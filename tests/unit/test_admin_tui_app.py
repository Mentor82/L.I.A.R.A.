from datetime import datetime, timedelta

import asyncio

from frontend.admin_tui.app import AdminDashboardScreen, AuditTimelinePane, SessionViewerPane
from frontend.admin_tui.models import AuditEvent, DecisionDelta, RunEntry, SessionSnapshot


def _make_session(session_id: str, *, minutes_offset: int) -> SessionSnapshot:
    timestamp = datetime.now() + timedelta(minutes=minutes_offset)
    return SessionSnapshot(
        session_id=session_id,
        created_at=timestamp,
        run_count=1,
        runs=[
            RunEntry(
                run_id=f"run-{session_id}",
                session_id=session_id,
                timestamp=timestamp,
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
    )


class TestSessionViewerPaneHelpers:
    def test_selected_session_defaults_to_first_loaded_session(self):
        pane = SessionViewerPane()
        pane._sessions = [_make_session("session-a", minutes_offset=0), _make_session("session-b", minutes_offset=1)]

        selected = pane._selected_session()

        assert selected is not None
        assert selected.session_id == "session-a"

    def test_session_selection_wraps_with_prev_and_next(self):
        pane = SessionViewerPane()
        pane._sessions = [
            _make_session("session-a", minutes_offset=0),
            _make_session("session-b", minutes_offset=1),
            _make_session("session-c", minutes_offset=2),
        ]

        pane._select_next_session()
        assert pane._selected_session().session_id == "session-b"

        pane._select_next_session()
        assert pane._selected_session().session_id == "session-c"

        pane._select_next_session()
        assert pane._selected_session().session_id == "session-a"

        pane._select_previous_session()
        assert pane._selected_session().session_id == "session-c"

    def test_keyboard_actions_change_selected_session(self, monkeypatch):
        pane = SessionViewerPane()
        pane._sessions = [
            _make_session("session-a", minutes_offset=0),
            _make_session("session-b", minutes_offset=1),
        ]
        monkeypatch.setattr(pane, "_render_sessions", lambda: None)

        pane.action_next_session()
        assert pane._selected_session().session_id == "session-b"

        pane.action_previous_session()
        assert pane._selected_session().session_id == "session-a"

    def test_build_selected_audit_output_path_sanitizes_session_id(self):
        pane = SessionViewerPane()

        path = pane._build_selected_audit_output_path("session:alpha/beta")

        assert path.name == "session_audit_session_alpha_beta.json"
        assert path.parent.name == "latest"

    def test_selection_propagates_to_screen(self, monkeypatch):
        pane = SessionViewerPane()
        pane._sessions = [
            _make_session("session-a", minutes_offset=0),
            _make_session("session-b", minutes_offset=1),
        ]
        monkeypatch.setattr(pane, "_render_sessions", lambda: None)
        monkeypatch.setattr(pane, "_refresh_linked_audit_timeline", lambda: None)

        class _FakeScreen:
            def __init__(self):
                self.selected_session_id = None

            def set_selected_session_id(self, session_id):
                self.selected_session_id = session_id

        fake_screen = _FakeScreen()
        monkeypatch.setattr(SessionViewerPane, "screen", property(lambda self: fake_screen))

        pane.action_next_session()

        assert fake_screen.selected_session_id == "session-b"

    def test_sync_selection_prefers_persisted_session_id(self, monkeypatch):
        pane = SessionViewerPane()
        pane._sessions = [
            _make_session("session-a", minutes_offset=0),
            _make_session("session-b", minutes_offset=1),
            _make_session("session-c", minutes_offset=2),
        ]

        class _FakeScreen:
            selected_session_id = "session-b"

        monkeypatch.setattr(SessionViewerPane, "screen", property(lambda self: _FakeScreen()))

        pane._sync_selected_session_index()

        assert pane._selected_session_index == 1


class TestAuditTimelinePaneHelpers:
    def test_refresh_uses_selected_session_filter(self, monkeypatch):
        pane = AuditTimelinePane()
        captured = {}

        class _FakeScreen:
            selected_session_id = "session-b"

        class _FakeWidget:
            def __init__(self):
                self.content = ""

            def update(self, text):
                self.content = text

        widget = _FakeWidget()

        def _fake_load_audit_events(session_id, limit, event_types):
            captured["session_id"] = session_id
            captured["limit"] = limit
            captured["event_types"] = event_types
            return []

        monkeypatch.setattr(AuditTimelinePane, "screen", property(lambda self: _FakeScreen()))
        monkeypatch.setattr(pane, "query_one", lambda selector, widget_type: widget)
        monkeypatch.setattr(pane.data_layer, "load_audit_events", _fake_load_audit_events)

        asyncio.run(pane._refresh())

        assert captured == {"session_id": "session-b", "limit": 50, "event_types": None}
        assert "Active session filter: session-b" in widget.content

    def test_refresh_passes_event_type_filter(self, monkeypatch):
        pane = AuditTimelinePane()
        captured = {}

        class _FakeScreen:
            selected_session_id = None

        class _FakeWidget:
            def __init__(self):
                self.content = ""

            def update(self, text):
                self.content = text

        widget = _FakeWidget()

        def _fake_load_audit_events(session_id, limit, event_types):
            captured["session_id"] = session_id
            captured["limit"] = limit
            captured["event_types"] = event_types
            return []

        pane._event_type_filter_index = 2  # score_feedback
        monkeypatch.setattr(AuditTimelinePane, "screen", property(lambda self: _FakeScreen()))
        monkeypatch.setattr(pane, "query_one", lambda selector, widget_type: widget)
        monkeypatch.setattr(pane.data_layer, "load_audit_events", _fake_load_audit_events)

        asyncio.run(pane._refresh())

        assert captured == {"session_id": None, "limit": 50, "event_types": ["score_feedback"]}
        assert "Event type filter: score_feedback" in widget.content

    def test_refresh_applies_time_window_filter(self, monkeypatch):
        pane = AuditTimelinePane()

        class _FakeScreen:
            selected_session_id = None

        class _FakeWidget:
            def __init__(self):
                self.content = ""

            def update(self, text):
                self.content = text

        widget = _FakeWidget()
        now = datetime.now()
        fresh = AuditEvent(
            event_id="fresh",
            session_id="session-a",
            run_id=None,
            timestamp=now - timedelta(minutes=2),
            event_type="repair",
            level="info",
            message="fresh event",
        )
        old = AuditEvent(
            event_id="old",
            session_id="session-a",
            run_id=None,
            timestamp=now - timedelta(minutes=20),
            event_type="repair",
            level="info",
            message="old event",
        )

        pane._time_window_filter_index = 1  # 5m
        monkeypatch.setattr(AuditTimelinePane, "screen", property(lambda self: _FakeScreen()))
        monkeypatch.setattr(pane, "query_one", lambda selector, widget_type: widget)
        monkeypatch.setattr(pane.data_layer, "load_audit_events", lambda session_id, limit, event_types: [fresh, old])

        asyncio.run(pane._refresh())

        assert "fresh event" in widget.content
        assert "old event" not in widget.content
        assert "Time window: 5m" in widget.content


class TestAdminDashboardScreenPersistence:
    def test_screen_loads_persisted_ui_state(self, monkeypatch):
        monkeypatch.setattr(
            "frontend.admin_tui.app.AdminDataLayer.load_admin_ui_state",
            lambda self: {
                "selected_session_id": "session-restore",
                "audit_event_filter_index": 3,
                "audit_time_window_filter_index": 2,
            },
        )

        screen = AdminDashboardScreen()

        assert screen.selected_session_id == "session-restore"
        assert screen.audit_event_filter_index == 3
        assert screen.audit_time_window_filter_index == 2

    def test_screen_persists_selected_session_and_filters(self, monkeypatch):
        captured_states = []

        monkeypatch.setattr("frontend.admin_tui.app.AdminDataLayer.load_admin_ui_state", lambda self: {})
        monkeypatch.setattr(
            "frontend.admin_tui.app.AdminDataLayer.save_admin_ui_state",
            lambda self, state: captured_states.append(dict(state)) or True,
        )

        screen = AdminDashboardScreen()
        screen.set_selected_session_id("session-live")
        screen.set_audit_filter_defaults(4, 1)

        assert captured_states, "expected persisted state writes"
        assert captured_states[-1]["selected_session_id"] == "session-live"
        assert captured_states[-1]["audit_event_filter_index"] == 4
        assert captured_states[-1]["audit_time_window_filter_index"] == 1
