#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Textual Worker Monitor for LIARA backend services."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx

from services.cli.main import DEFAULT_BASE_URL, DEFAULT_HTTP_TIMEOUT
from services.tui.shared import bool_style, load_textual_symbols, status_style


@dataclass
class WorkerRow:
    """Normalized backend row for table rendering."""

    name: str
    configured: bool
    live_status: str


def _build_rows(configured: dict[str, Any], live: dict[str, Any]) -> list[WorkerRow]:
    names = sorted(set(configured.keys()) | set(live.keys()))
    return [
        WorkerRow(
            name=name,
            configured=bool(configured.get(name, False)),
            live_status=str(live.get(name, "unavailable")),
        )
        for name in names
    ]


def create_worker_monitor_app(base_url: str, timeout: float, interval_seconds: float):
    """Factory to create the Textual app without importing Textual at module import time."""
    app_mod, binding_mod, containers_mod, widgets_mod = load_textual_symbols("services.tui.worker_monitor")
    App = getattr(app_mod, "App")
    Binding = getattr(binding_mod, "Binding")
    Vertical = getattr(containers_mod, "Vertical")
    Footer = getattr(widgets_mod, "Footer")
    Header = getattr(widgets_mod, "Header")
    RichLog = getattr(widgets_mod, "RichLog")
    DataTable = getattr(widgets_mod, "DataTable")

    class WorkerMonitorApp(App):
        """LIARA worker monitor with live backend health polling."""

        TITLE = "LIARA Worker Monitor"
        SUB_TITLE = "Backend health and readiness"
        CSS = """
        Screen {
            layout: vertical;
        }

        #main {
            height: 1fr;
        }

        #summary {
            height: auto;
            border: solid #3d4c63;
            padding: 1 2;
        }

        #workers {
            height: 60%;
        }

        #events {
            height: 40%;
            border: solid #3d4c63;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh"),
            Binding("a", "toggle_auto", "Auto"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.base_url = base_url.rstrip("/")
            self.timeout = timeout
            self.interval_seconds = max(1.0, interval_seconds)
            self.auto_refresh_enabled = True
            self._inflight = False
            self._last_refresh_label = "never"
            self._last_error = "none"

        def compose(self):
            yield Header(show_clock=True)
            with Vertical(id="main"):
                yield RichLog(id="summary", highlight=False, wrap=True)
                yield DataTable(id="workers", cursor_type="row")
                yield RichLog(id="events", highlight=True, wrap=True)
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#workers", DataTable)
            table.add_columns("Backend", "Configured", "Live")
            table.zebra_stripes = True
            summary = self.query_one("#summary", RichLog)
            summary.write(
                f"API={self.base_url} auto-refresh=on interval={self.interval_seconds:.1f}s last-refresh=never"
            )
            events = self.query_one("#events", RichLog)
            events.write(f"[cyan]Monitoring[/cyan] {self.base_url} every {self.interval_seconds:.1f}s")
            self.set_interval(self.interval_seconds, self._tick)
            self.call_later(self._schedule_refresh)

        def action_refresh(self) -> None:
            self._schedule_refresh()

        def action_toggle_auto(self) -> None:
            self.auto_refresh_enabled = not self.auto_refresh_enabled
            mode = "on" if self.auto_refresh_enabled else "off"
            self.query_one("#events", RichLog).write(f"[yellow]auto-refresh -> {mode}[/yellow]")
            self._update_summary()

        def _tick(self) -> None:
            if self.auto_refresh_enabled:
                self._schedule_refresh()

        def _update_summary(self) -> None:
            mode = "on" if self.auto_refresh_enabled else "off"
            summary = self.query_one("#summary", RichLog)
            summary.clear()
            summary.write(
                f"API={self.base_url} auto-refresh={mode} interval={self.interval_seconds:.1f}s "
                f"last-refresh={self._last_refresh_label} last-error={self._last_error}"
            )

        def _schedule_refresh(self) -> None:
            if self._inflight:
                return
            self._inflight = True
            asyncio.create_task(self._refresh_health())

        async def _refresh_health(self) -> None:
            table = self.query_one("#workers", DataTable)
            events = self.query_one("#events", RichLog)

            try:
                async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
                    health_resp = await client.get("/health")
                    health_resp.raise_for_status()
                    health_data = health_resp.json()

                    backends_resp = await client.get("/health/backends")
                    backends_resp.raise_for_status()
                    backends_data = backends_resp.json()

                configured = health_data.get("backends_configured", {})
                live = backends_data.get("backend_health", {})
                rows = _build_rows(configured, live)

                table.clear(columns=False)
                for row in rows:
                    table.add_row(
                        row.name,
                        bool_style(row.configured),
                        status_style(row.live_status),
                    )

                overall = str(health_data.get("status", "unknown"))
                now = datetime.now().strftime("%H:%M:%S")
                self._last_refresh_label = now
                self._last_error = "none"
                self._update_summary()
                events.write(f"[{now}] status={overall}; backends={len(rows)}")
            except Exception as exc:
                now = datetime.now().strftime("%H:%M:%S")
                self._last_refresh_label = now
                self._last_error = str(exc)
                self._update_summary()
                events.write(f"[{now}] [red]refresh failed:[/red] {exc}")
            finally:
                self._inflight = False

    return WorkerMonitorApp


def run_worker_monitor(base_url: str = DEFAULT_BASE_URL, timeout: float = DEFAULT_HTTP_TIMEOUT, interval_seconds: float = 3.0) -> int:
    app_cls = create_worker_monitor_app(base_url, timeout, interval_seconds)
    app = app_cls()
    app.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Textual worker monitor for liara-api")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_HTTP_TIMEOUT)
    parser.add_argument("--interval", type=float, default=3.0, help="poll interval in seconds")
    args = parser.parse_args(argv)

    return run_worker_monitor(base_url=args.base_url, timeout=args.timeout, interval_seconds=args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
