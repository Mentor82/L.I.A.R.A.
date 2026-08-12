#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Textual Admin Console for LIARA."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from uuid import uuid4
from typing import Any

import httpx

from services.cli.main import DEFAULT_BASE_URL, DEFAULT_HTTP_TIMEOUT, DEFAULT_USER_ID
from services.tui.shared import bool_style, load_textual_symbols, status_style


def assurance_style(verdict: str, *, required: bool = False) -> str:
    normalized = str(verdict or "pending").lower()
    if normalized == "passed":
        return "[bold green]passed[/bold green]"
    if normalized == "failed":
        return "[bold red]failed[/bold red]"
    if normalized == "attention":
        return "[yellow]attention[/yellow]"
    label = "pending*" if required else "pending"
    return f"[cyan]{label}[/cyan]"


def _coverage_label(value: Any) -> str:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{round(max(0.0, min(ratio, 1.0)) * 100):d}%"


def dreaming_proposal_row(proposal: dict[str, Any]) -> tuple[str, ...]:
    assurance = proposal.get("assurance") if isinstance(proposal.get("assurance"), dict) else {}
    quality = proposal.get("quality_signals") if isinstance(proposal.get("quality_signals"), dict) else {}
    complexity = quality.get("complexity") if isinstance(quality.get("complexity"), dict) else {}
    coverage = quality.get("coverage") if isinstance(quality.get("coverage"), dict) else {}
    proposal_id = str(proposal.get("proposal_id") or "-")
    session_id = str(proposal.get("session_id") or "-")
    job_id = str(assurance.get("validator_job_id") or "-")
    artifacts = assurance.get("artifacts") if isinstance(assurance.get("artifacts"), list) else []
    first_artifact = "-"
    if artifacts and isinstance(artifacts[0], dict):
        first_artifact = str(artifacts[0].get("path") or "-")
        if len(first_artifact) > 36:
            first_artifact = "..." + first_artifact[-33:]
    return (
        proposal_id[:18],
        session_id[:18],
        str(proposal.get("decision") or "pending"),
        assurance_style(str(assurance.get("verdict") or "pending"), required=bool(assurance.get("required"))),
        job_id[:18],
        str(assurance.get("findings_count") or 0),
        str(complexity.get("level") or "-") if quality.get("available") else "-",
        _coverage_label(coverage.get("source_coverage_ratio")),
        _coverage_label(coverage.get("relation_coverage_ratio")),
        first_artifact,
    )


def create_admin_console_app(base_url: str, timeout: float, interval_seconds: float, user_id: str):
    """Create admin console app class."""
    app_mod, binding_mod, containers_mod, widgets_mod = load_textual_symbols("services.tui.admin_console")
    App = getattr(app_mod, "App")
    Binding = getattr(binding_mod, "Binding")
    Vertical = getattr(containers_mod, "Vertical")
    Horizontal = getattr(containers_mod, "Horizontal")
    Header = getattr(widgets_mod, "Header")
    Footer = getattr(widgets_mod, "Footer")
    Static = getattr(widgets_mod, "Static")
    RichLog = getattr(widgets_mod, "RichLog")
    DataTable = getattr(widgets_mod, "DataTable")

    class AdminConsoleApp(App):
        """System overview for LIARA services and tools."""

        TITLE = "LIARA Admin Console"
        SUB_TITLE = "Health, tools, and Dreaming assurance"

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

        #tables {
            height: 45%;
            layout: horizontal;
        }

        #backends {
            width: 1fr;
        }

        #tools {
            width: 2fr;
        }

        #dreaming {
            height: 35%;
        }

        #events {
            height: 20%;
            border: solid #3d4c63;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh"),
            Binding("a", "toggle_auto", "Auto"),
            Binding("v", "view_selection", "View row"),
            Binding("n", "seed_session", "Seed Session"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.base_url = base_url.rstrip("/")
            self.timeout = timeout
            self.interval_seconds = max(1.0, interval_seconds)
            self.user_id = user_id
            self.auto_refresh_enabled = True
            self._inflight = False
            self._interval_handle = None
            self._dreaming_rows: list[dict[str, Any]] = []

        def compose(self):
            yield Header(show_clock=True)
            with Vertical(id="main"):
                yield Static(id="summary")
                with Horizontal(id="tables"):
                    yield DataTable(id="backends", cursor_type="row")
                    yield DataTable(id="tools", cursor_type="row")
                yield DataTable(id="dreaming", cursor_type="row")
                yield RichLog(id="events", highlight=True, wrap=True)
            yield Footer()

        def on_mount(self) -> None:
            backends = self.query_one("#backends", DataTable)
            backends.add_columns("Backend", "Configured", "Live")
            backends.zebra_stripes = True

            tools = self.query_one("#tools", DataTable)
            tools.add_columns("Tool", "Required Params", "Optional Params")
            tools.zebra_stripes = True

            dreaming = self.query_one("#dreaming", DataTable)
            dreaming.add_column("Proposal", width=18)
            dreaming.add_column("Session", width=18)
            dreaming.add_column("Decision", width=10)
            dreaming.add_column("Assurance", width=12)
            dreaming.add_column("Validator Job", width=18)
            dreaming.add_column("Findings", width=8)
            dreaming.add_column("Complexity", width=10)
            dreaming.add_column("Src Cov", width=7)
            dreaming.add_column("Rel Cov", width=7)
            dreaming.add_column("Artifact", width=36)
            dreaming.zebra_stripes = True

            summary = self.query_one("#summary", Static)
            summary.update(
                f"[bold cyan]API[/bold cyan]: {self.base_url}    "
                f"[bold cyan]Auto-refresh[/bold cyan]: on    "
                f"[bold cyan]Interval[/bold cyan]: {self.interval_seconds:.1f}s"
            )

            events = self.query_one("#events", RichLog)
            events.write(f"[cyan]Admin console online[/cyan] {self.base_url}")

            self._interval_handle = self.set_interval(self.interval_seconds, self._tick)
            self.call_later(self._schedule_refresh)

        def _tick(self) -> None:
            if self.auto_refresh_enabled:
                self._schedule_refresh()

        def action_refresh(self) -> None:
            self._schedule_refresh()

        def action_toggle_auto(self) -> None:
            self.auto_refresh_enabled = not self.auto_refresh_enabled
            mode = "on" if self.auto_refresh_enabled else "off"
            summary = self.query_one("#summary", Static)
            summary.update(
                f"[bold cyan]API[/bold cyan]: {self.base_url}    "
                f"[bold cyan]Auto-refresh[/bold cyan]: {mode}    "
                f"[bold cyan]Interval[/bold cyan]: {self.interval_seconds:.1f}s"
            )
            events = self.query_one("#events", RichLog)
            events.write(f"[yellow]auto-refresh -> {mode}[/yellow]")

        def action_seed_session(self) -> None:
            asyncio.create_task(self._seed_session())

        def action_view_selection(self) -> None:
            events = self.query_one("#events", RichLog)
            backends = self.query_one("#backends", DataTable)
            tools = self.query_one("#tools", DataTable)
            dreaming = self.query_one("#dreaming", DataTable)

            if backends.has_focus:
                row = backends.get_row_at(backends.cursor_row)
                events.write(
                    f"[cyan]backend view[/cyan]: name={row[0]} configured={row[1]} live={row[2]}"
                )
                return

            if tools.has_focus:
                row = tools.get_row_at(tools.cursor_row)
                events.write(
                    f"[cyan]tool view[/cyan]: name={row[0]} required={row[1]} optional={row[2]}"
                )
                return

            if dreaming.has_focus and 0 <= dreaming.cursor_row < len(self._dreaming_rows):
                proposal = self._dreaming_rows[dreaming.cursor_row]
                assurance = proposal.get("assurance") or {}
                quality = proposal.get("quality_signals") or {}
                complexity = quality.get("complexity") or {}
                coverage = quality.get("coverage") or {}
                artifacts = [item.get("path") for item in assurance.get("artifacts", []) if isinstance(item, dict)]
                audit = assurance.get("audit_reference") or {}
                events.write(
                    "[cyan]dreaming assurance[/cyan]: "
                    f"proposal={proposal.get('proposal_id')} decision={proposal.get('decision')} "
                    f"required={assurance.get('required')} verdict={assurance.get('verdict')} "
                    f"blocked={assurance.get('blocked')} job={assurance.get('validator_job_id')} "
                    f"findings={assurance.get('findings_count')} severity={assurance.get('highest_severity')} "
                    f"artifacts={artifacts or '-'} audit={audit or '-'}"
                )
                events.write(
                    "[cyan]dreaming quality[/cyan]: "
                    f"available={quality.get('available')} schema={quality.get('schema_version')} "
                    f"complexity={complexity.get('level')} score={complexity.get('score')} "
                    f"chars={complexity.get('character_count')} lines={complexity.get('line_count')} "
                    f"sources={complexity.get('declared_source_count')} evidence={complexity.get('evidence_count')} "
                    f"relations={complexity.get('accepted_relation_count')} "
                    f"source_coverage={coverage.get('source_coverage_ratio')} "
                    f"relation_coverage={coverage.get('relation_coverage_ratio')} "
                    f"uncovered={coverage.get('uncovered_source_ids') or '-'} "
                    f"relation_uncovered={coverage.get('relation_uncovered_source_ids') or '-'}"
                )
                return

            events.write("[yellow]view[/yellow]: focus backends, tools, or dreaming table first")

        async def _seed_session(self) -> None:
            events = self.query_one("#events", RichLog)
            session_id = f"admin-{uuid4().hex[:8]}"
            payload = {
                "session_id": session_id,
                "user_id": self.user_id,
                "metadata": {"source": "admin_console"},
            }
            try:
                async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
                    resp = await client.post("/session", json=payload)
                    resp.raise_for_status()
                events.write(f"[green]seeded session:[/green] {session_id}")
            except Exception as exc:
                events.write(f"[red]seed session failed:[/red] {exc}")

        def _schedule_refresh(self) -> None:
            if self._inflight:
                return
            self._inflight = True
            asyncio.create_task(self._refresh())

        async def _refresh(self) -> None:
            summary = self.query_one("#summary", Static)
            backends_table = self.query_one("#backends", DataTable)
            tools_table = self.query_one("#tools", DataTable)
            dreaming_table = self.query_one("#dreaming", DataTable)
            events = self.query_one("#events", RichLog)

            try:
                async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
                    health_resp = await client.get("/health")
                    health_resp.raise_for_status()
                    health_data = health_resp.json()

                    backends_resp = await client.get("/health/backends")
                    backends_resp.raise_for_status()
                    backend_data = backends_resp.json()

                    tools_resp = await client.get("/tools")
                    tools_resp.raise_for_status()
                    tools_data = tools_resp.json()

                    try:
                        dreaming_resp = await client.get(
                            "/operations/dreaming",
                            params={"decision": "all", "limit": 50},
                        )
                        dreaming_resp.raise_for_status()
                        dreaming_data = dreaming_resp.json()
                    except Exception as exc:
                        dreaming_data = {
                            "status": "failed",
                            "error": str(exc),
                            "pending_staged_items": 0,
                            "pending_proposals": 0,
                            "proposals": [],
                            "assurance": {"blocked": 0, "verdicts": {}},
                            "quality_signals": {"available": 0, "complexity_levels": {}},
                        }

                configured = health_data.get("backends_configured", {})
                live = backend_data.get("backend_health", {})
                memory_mode = str(health_data.get("memory_mode", "unknown"))
                service_status = str(health_data.get("status", "unknown"))
                tool_count = int(tools_data.get("count", 0) or 0)
                auto_mode = "on" if self.auto_refresh_enabled else "off"
                assurance_summary = dreaming_data.get("assurance") or {}
                verdicts = assurance_summary.get("verdicts") or {}
                pending_staged = int(dreaming_data.get("pending_staged_items") or 0)
                pending_proposals = int(dreaming_data.get("pending_proposals") or 0)
                assurance_blocked = int(assurance_summary.get("blocked") or 0)
                quality_summary = dreaming_data.get("quality_signals") or {}
                quality_levels = quality_summary.get("complexity_levels") or {}
                quality_available = int(quality_summary.get("available") or 0)

                summary.update(
                    f"[bold cyan]API[/bold cyan]: {self.base_url}    "
                    f"[bold cyan]Status[/bold cyan]: {service_status}    "
                    f"[bold cyan]Memory[/bold cyan]: {memory_mode}    "
                    f"[bold cyan]Tools[/bold cyan]: {tool_count}    "
                    f"[bold cyan]Dreaming[/bold cyan]: {pending_staged} staged / {pending_proposals} pending    "
                    f"[bold cyan]Assurance[/bold cyan]: {verdicts.get('passed', 0)} passed / {assurance_blocked} blocked    "
                    f"[bold cyan]Quality[/bold cyan]: {quality_available} signals / {quality_levels.get('high', 0)} high    "
                    f"[bold cyan]Auto-refresh[/bold cyan]: {auto_mode}"
                )

                all_backend_names = sorted(set(configured.keys()) | set(live.keys()))
                backends_table.clear(columns=False)
                for name in all_backend_names:
                    backends_table.add_row(
                        name,
                        bool_style(bool(configured.get(name, False))),
                        status_style(str(live.get(name, "unavailable"))),
                    )

                tools_table.clear(columns=False)
                for tool in tools_data.get("tools", []):
                    tool_name = str(tool.get("name") or "-")
                    req = tool.get("required_parameters") or []
                    opt = tool.get("optional_parameters") or []
                    req_text = ", ".join(req) if req else "-"
                    opt_text = ", ".join(opt) if opt else "-"
                    tools_table.add_row(tool_name, req_text, opt_text)

                self._dreaming_rows = [
                    item for item in dreaming_data.get("proposals", []) if isinstance(item, dict)
                ]
                dreaming_table.clear(columns=False)
                for proposal in self._dreaming_rows:
                    dreaming_table.add_row(*dreaming_proposal_row(proposal))

                now = datetime.now().strftime("%H:%M:%S")
                events.write(
                    f"[{now}] refreshed: backends={len(all_backend_names)} tools={tool_count} "
                    f"proposals={len(self._dreaming_rows)} assurance_blocked={assurance_blocked}"
                )
            except Exception as exc:
                now = datetime.now().strftime("%H:%M:%S")
                events.write(f"[{now}] [red]refresh failed:[/red] {exc}")
            finally:
                self._inflight = False

    return AdminConsoleApp


def run_admin_console(
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_HTTP_TIMEOUT,
    interval_seconds: float = 5.0,
    user_id: str = DEFAULT_USER_ID,
) -> int:
    app_cls = create_admin_console_app(base_url, timeout, interval_seconds, user_id)
    app = app_cls()
    app.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Textual admin console for liara-api")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_HTTP_TIMEOUT)
    parser.add_argument("--interval", type=float, default=5.0, help="poll interval in seconds")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    args = parser.parse_args(argv)

    return run_admin_console(
        base_url=args.base_url,
        timeout=args.timeout,
        interval_seconds=args.interval,
        user_id=args.user_id,
    )


if __name__ == "__main__":
    raise SystemExit(main())
