#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Textual Memory Inspector for LIARA sessions."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from services.cli.main import DEFAULT_BASE_URL, DEFAULT_HTTP_TIMEOUT, DEFAULT_USER_ID
from services.tui.shared import load_textual_symbols


@dataclass
class HistoryRow:
    """History row prepared for rendering."""

    created_at: str
    role: str
    run_id: str
    content_preview: str


def _format_preview(content: str, max_len: int = 120) -> str:
    compact = " ".join((content or "").split())
    if len(compact) <= max_len:
        return compact
    return f"{compact[: max_len - 3]}..."


def create_memory_inspector_app(
    *,
    base_url: str,
    timeout: float,
    interval_seconds: float,
    session_id: str,
    user_id: str,
    limit: int,
):
    """Create Textual app class for memory inspection."""
    app_mod, binding_mod, containers_mod, widgets_mod = load_textual_symbols("services.tui.memory_inspector")
    App = getattr(app_mod, "App")
    Binding = getattr(binding_mod, "Binding")
    Vertical = getattr(containers_mod, "Vertical")
    Horizontal = getattr(containers_mod, "Horizontal")
    Header = getattr(widgets_mod, "Header")
    Footer = getattr(widgets_mod, "Footer")
    Static = getattr(widgets_mod, "Static")
    RichLog = getattr(widgets_mod, "RichLog")
    DataTable = getattr(widgets_mod, "DataTable")

    class MemoryInspectorApp(App):
        """Session-centric memory inspector with live polling."""

        TITLE = "LIARA Memory Inspector"
        SUB_TITLE = "Session snapshot and conversation history"

        CSS = """
        Screen {
            layout: vertical;
        }

        #main {
            height: 1fr;
        }

        #meta {
            height: auto;
            border: solid #3d4c63;
            padding: 1 2;
        }

        #history {
            height: 1fr;
        }

        #events {
            height: 30%;
            border: solid #3d4c63;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh"),
            Binding("a", "toggle_auto", "Auto"),
            Binding("m", "toggle_tools", "Toggle tool msgs"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.base_url = base_url.rstrip("/")
            self.timeout = timeout
            self.interval_seconds = max(1.0, interval_seconds)
            self.session_id = session_id
            self.user_id = user_id
            self.limit = max(1, min(500, limit))
            self.include_tool_messages = True
            self.auto_refresh_enabled = True
            self._inflight = False
            self._last_refresh_label = "never"
            self._last_error = "none"

        def compose(self):
            yield Header(show_clock=True)
            with Vertical(id="main"):
                yield Static(id="meta")
                yield DataTable(id="history", cursor_type="row")
                yield RichLog(id="events", highlight=True, wrap=True)
            yield Footer()

        def on_mount(self) -> None:
            history = self.query_one("#history", DataTable)
            history.add_columns("Time", "Role", "Run", "Content")
            history.zebra_stripes = True

            meta = self.query_one("#meta", Static)
            meta.update(
                f"[bold cyan]Session[/bold cyan]: {self.session_id}    "
                f"[bold cyan]User[/bold cyan]: {self.user_id}    "
                f"[bold cyan]Limit[/bold cyan]: {self.limit}    "
                f"[bold cyan]Tool messages[/bold cyan]: on    "
                f"[bold cyan]Auto-refresh[/bold cyan]: on"
            )

            events = self.query_one("#events", RichLog)
            events.write(f"[cyan]Inspecting[/cyan] {self.base_url} session={self.session_id}")

            self.set_interval(self.interval_seconds, self._tick)
            self.call_later(self._schedule_refresh)

        def action_refresh(self) -> None:
            self._schedule_refresh()

        def action_toggle_auto(self) -> None:
            self.auto_refresh_enabled = not self.auto_refresh_enabled
            mode = "on" if self.auto_refresh_enabled else "off"
            self.query_one("#events", RichLog).write(f"[yellow]auto-refresh -> {mode}[/yellow]")
            self._update_meta_baseline()

        def action_toggle_tools(self) -> None:
            self.include_tool_messages = not self.include_tool_messages
            mode = "on" if self.include_tool_messages else "off"
            events = self.query_one("#events", RichLog)
            events.write(f"[yellow]tool messages -> {mode}[/yellow]")
            self._update_meta_baseline()
            self._schedule_refresh()

        def _tick(self) -> None:
            if self.auto_refresh_enabled:
                self._schedule_refresh()

        def _update_meta_baseline(self) -> None:
            tool_mode = "on" if self.include_tool_messages else "off"
            auto_mode = "on" if self.auto_refresh_enabled else "off"
            meta = self.query_one("#meta", Static)
            meta.update(
                f"[bold cyan]Session[/bold cyan]: {self.session_id}    "
                f"[bold cyan]User[/bold cyan]: {self.user_id}    "
                f"[bold cyan]Limit[/bold cyan]: {self.limit}    "
                f"[bold cyan]Tool messages[/bold cyan]: {tool_mode}    "
                f"[bold cyan]Auto-refresh[/bold cyan]: {auto_mode}    "
                f"[bold cyan]Last refresh[/bold cyan]: {self._last_refresh_label}    "
                f"[bold cyan]Last error[/bold cyan]: {self._last_error}"
            )

        def _schedule_refresh(self) -> None:
            if self._inflight:
                return
            self._inflight = True
            asyncio.create_task(self._refresh())

        async def _refresh(self) -> None:
            meta = self.query_one("#meta", Static)
            history_table = self.query_one("#history", DataTable)
            events = self.query_one("#events", RichLog)

            try:
                async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
                    session_resp = await client.get(
                        "/session",
                        params={"session_id": self.session_id, "user_id": self.user_id},
                    )
                    session_resp.raise_for_status()
                    session_data = session_resp.json()

                    history_resp = await client.get(
                        "/history",
                        params={
                            "session_id": self.session_id,
                            "limit": self.limit,
                            "include_tool_messages": str(self.include_tool_messages).lower(),
                        },
                    )
                    history_resp.raise_for_status()
                    history_data = history_resp.json()

                message_count = int(session_data.get("message_count", 0) or 0)
                updated_at = str(session_data.get("updated_at") or "-")
                last_run_id = str(session_data.get("last_run_id") or "-")
                mode = "on" if self.include_tool_messages else "off"

                meta.update(
                    f"[bold cyan]Session[/bold cyan]: {self.session_id}    "
                    f"[bold cyan]User[/bold cyan]: {self.user_id}    "
                    f"[bold cyan]Msgs[/bold cyan]: {message_count}    "
                    f"[bold cyan]Last run[/bold cyan]: {last_run_id}    "
                    f"[bold cyan]Updated[/bold cyan]: {updated_at}    "
                    f"[bold cyan]Tool messages[/bold cyan]: {mode}"
                )

                items = history_data.get("items", [])
                rows: list[HistoryRow] = []
                for item in items:
                    rows.append(
                        HistoryRow(
                            created_at=str(item.get("created_at") or "-"),
                            role=str(item.get("role") or "?"),
                            run_id=str(item.get("run_id") or "-"),
                            content_preview=_format_preview(str(item.get("content") or "")),
                        )
                    )

                history_table.clear(columns=False)
                for row in rows:
                    history_table.add_row(row.created_at, row.role, row.run_id, row.content_preview)

                now = datetime.now().strftime("%H:%M:%S")
                self._last_refresh_label = now
                self._last_error = "none"
                self._update_meta_baseline()
                events.write(f"[{now}] refreshed; rows={len(rows)}")
            except Exception as exc:
                now = datetime.now().strftime("%H:%M:%S")
                self._last_refresh_label = now
                self._last_error = str(exc)
                self._update_meta_baseline()
                events.write(f"[{now}] [red]refresh failed:[/red] {exc}")
            finally:
                self._inflight = False

    return MemoryInspectorApp


def run_memory_inspector(
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
    interval_seconds: float = 3.0,
    session_id: str,
    user_id: str,
    limit: int = 100,
) -> int:
    app_cls = create_memory_inspector_app(
        base_url=base_url,
        timeout=timeout,
        interval_seconds=interval_seconds,
        session_id=session_id,
        user_id=user_id,
        limit=limit,
    )
    app = app_cls()
    app.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Textual memory inspector for liara-api sessions")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_HTTP_TIMEOUT)
    parser.add_argument("--interval", type=float, default=3.0, help="poll interval in seconds")
    parser.add_argument("--session-id", required=True, help="session to inspect")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="user id for /session endpoint")
    parser.add_argument("--limit", type=int, default=100, help="history rows to fetch (1..500)")
    args = parser.parse_args(argv)

    return run_memory_inspector(
        base_url=args.base_url,
        timeout=args.timeout,
        interval_seconds=args.interval,
        session_id=args.session_id,
        user_id=args.user_id,
        limit=args.limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
