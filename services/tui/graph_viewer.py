#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Textual Graph Viewer for LIARA — live Neo4j schema and Cypher REPL."""

from __future__ import annotations

import argparse
import asyncio
import importlib
from datetime import datetime
from typing import Any

# ──────────────────────────────────────────────────────────────────────────────
# Defaults (mirror .env)
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_NEO4J_URL = "bolt://127.0.0.1:7688"
DEFAULT_NEO4J_USER = "neo4j"
DEFAULT_NEO4J_PASSWORD = "liara2026"
DEFAULT_INTERVAL = 10.0

# ──────────────────────────────────────────────────────────────────────────────
# Lazy loaders
# ──────────────────────────────────────────────────────────────────────────────

def _load_neo4j_driver():
    """Import neo4j.AsyncGraphDatabase at runtime; give a clear install hint."""
    try:
        neo4j_mod = importlib.import_module("neo4j")
    except ImportError as exc:
        raise RuntimeError(
            "neo4j driver is required for services.tui.graph_viewer. "
            "Install it with: pip install neo4j"
        ) from exc
    AsyncGraphDatabase = getattr(neo4j_mod, "AsyncGraphDatabase")
    return AsyncGraphDatabase


def _load_textual_symbols() -> tuple[Any, ...]:
    """Load Textual classes at runtime with a clear install hint if missing."""
    try:
        app_mod = importlib.import_module("textual.app")
        binding_mod = importlib.import_module("textual.binding")
        containers_mod = importlib.import_module("textual.containers")
        widgets_mod = importlib.import_module("textual.widgets")
    except ImportError as exc:
        raise RuntimeError(
            "textual is required for services.tui.graph_viewer. "
            "Install it with: pip install textual"
        ) from exc

    App = getattr(app_mod, "App")
    Binding = getattr(binding_mod, "Binding")
    Vertical = getattr(containers_mod, "Vertical")
    Horizontal = getattr(containers_mod, "Horizontal")
    Header = getattr(widgets_mod, "Header")
    Footer = getattr(widgets_mod, "Footer")
    Static = getattr(widgets_mod, "Static")
    RichLog = getattr(widgets_mod, "RichLog")
    DataTable = getattr(widgets_mod, "DataTable")
    Input = getattr(widgets_mod, "Input")
    Label = getattr(widgets_mod, "Label")

    return App, Binding, Vertical, Horizontal, Header, Footer, Static, RichLog, DataTable, Input, Label


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _trunc(value: Any, max_len: int = 60) -> str:
    s = str(value) if value is not None else ""
    return s[:max_len] + "…" if len(s) > max_len else s


# ──────────────────────────────────────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────────────────────────────────────

def create_graph_viewer_app(
    neo4j_url: str,
    user: str,
    password: str,
    interval_seconds: float,
):
    """Return a Textual App class wired to the given Neo4j Bolt endpoint."""

    (
        App, Binding, Vertical, Horizontal, Header, Footer,
        Static, RichLog, DataTable, Input, Label,
    ) = _load_textual_symbols()

    AsyncGraphDatabase = _load_neo4j_driver()

    class GraphViewerApp(App):
        """LIARA Graph Viewer — Neo4j schema explorer and Cypher REPL."""

        TITLE = "LIARA Graph Viewer"
        SUB_TITLE = f"Neo4j  {neo4j_url}"

        CSS = """
        Screen {
            layout: vertical;
        }

        #conn_status {
            height: 1;
            padding: 0 1;
            background: $surface;
            color: $text-muted;
        }

        #schema {
            height: 40%;
            layout: horizontal;
        }

        #node_panel {
            width: 1fr;
            border: solid $primary-darken-2;
        }

        #rel_panel {
            width: 1fr;
            border: solid $primary-darken-2;
        }

        #node_label {
            height: 1;
            padding: 0 1;
            background: $primary-darken-2;
        }

        #rel_label {
            height: 1;
            padding: 0 1;
            background: $primary-darken-2;
        }

        #cypher_row {
            height: 3;
            padding: 0 1;
            layout: horizontal;
        }

        #cypher_input {
            width: 1fr;
        }

        #results {
            height: 1fr;
            border: solid $primary-darken-2;
        }

        #events {
            height: 6;
            border: solid $primary-darken-2;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh schema"),
            Binding("a", "toggle_auto", "Auto"),
            Binding("c", "focus_cypher", "Cypher input"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self._neo4j_url = neo4j_url
            self._user = user
            self._password = password
            self._interval = max(5.0, interval_seconds)
            self._auto_refresh_enabled = True
            self._inflight = False

        # ── Layout ──────────────────────────────────────────────────────────

        def compose(self):
            yield Header(show_clock=True)

            yield Static("Connecting…", id="conn_status")

            with Horizontal(id="schema"):
                with Vertical(id="node_panel"):
                    yield Label("Node Labels", id="node_label")
                    yield DataTable(id="node_table", cursor_type="row")

                with Vertical(id="rel_panel"):
                    yield Label("Relationship Types", id="rel_label")
                    yield DataTable(id="rel_table", cursor_type="row")

            with Horizontal(id="cypher_row"):
                yield Input(
                    placeholder="Cypher query — press Enter to run…",
                    id="cypher_input",
                )

            yield DataTable(id="results", cursor_type="row")
            yield RichLog(id="events", highlight=True, wrap=True)

            yield Footer()

        # ── Startup ─────────────────────────────────────────────────────────

        def on_mount(self) -> None:
            node_tbl = self.query_one("#node_table", DataTable)
            node_tbl.add_columns("Label", "Nodes")
            node_tbl.zebra_stripes = True

            rel_tbl = self.query_one("#rel_table", DataTable)
            rel_tbl.add_columns("Type", "Relationships")
            rel_tbl.zebra_stripes = True

            res_tbl = self.query_one("#results", DataTable)
            res_tbl.zebra_stripes = True

            events = self.query_one("#events", RichLog)
            events.write(f"Connecting to {self._neo4j_url}…")

            self.set_interval(self._interval, self._tick)
            self.call_later(self._schedule_refresh)

        # ── Actions ─────────────────────────────────────────────────────────

        def action_refresh(self) -> None:
            self._schedule_refresh()

        def action_toggle_auto(self) -> None:
            self._auto_refresh_enabled = not self._auto_refresh_enabled
            mode = "on" if self._auto_refresh_enabled else "off"
            status = self.query_one("#conn_status", Static)
            status.update(f"auto-refresh: {mode}  interval: {self._interval:.1f}s  last refresh: {_ts()}")
            self.query_one("#events", RichLog).write(f"[{_ts()}] [yellow]auto-refresh -> {mode}[/yellow]")

        def action_focus_cypher(self) -> None:
            self.query_one("#cypher_input", Input).focus()

        def _tick(self) -> None:
            if self._auto_refresh_enabled:
                self._schedule_refresh()

        def _schedule_refresh(self) -> None:
            if self._inflight:
                return
            self._inflight = True
            asyncio.create_task(self._refresh_schema())

        # ── Cypher submit ────────────────────────────────────────────────────

        def on_input_submitted(self, event) -> None:
            query = event.value.strip()
            if query:
                asyncio.create_task(self._run_cypher(query))

        # ── Neo4j helpers ────────────────────────────────────────────────────

        def _make_driver(self):
            return AsyncGraphDatabase.driver(
                self._neo4j_url, auth=(self._user, self._password)
            )

        async def _refresh_schema(self) -> None:
            status = self.query_one("#conn_status", Static)
            node_tbl = self.query_one("#node_table", DataTable)
            rel_tbl = self.query_one("#rel_table", DataTable)
            events = self.query_one("#events", RichLog)

            try:
                async with self._make_driver() as driver:
                    await driver.verify_connectivity()

                    # Node counts by label
                    node_records, _, _ = await driver.execute_query(
                        "MATCH (n) UNWIND labels(n) AS label "
                        "RETURN label, count(*) AS cnt ORDER BY cnt DESC",
                        database_="neo4j",
                    )

                    # Relationship counts by type
                    rel_records, _, _ = await driver.execute_query(
                        "MATCH ()-[r]->() "
                        "RETURN type(r) AS rtype, count(*) AS cnt ORDER BY cnt DESC",
                        database_="neo4j",
                    )

                node_tbl.clear(columns=False)
                for rec in node_records:
                    node_tbl.add_row(str(rec["label"]), str(rec["cnt"]))

                rel_tbl.clear(columns=False)
                for rec in rel_records:
                    rel_tbl.add_row(str(rec["rtype"]), str(rec["cnt"]))

                total_nodes = sum(rec["cnt"] for rec in node_records)
                total_rels = sum(rec["cnt"] for rec in rel_records)

                status.update(
                    f"[bold green]connected[/bold green]  "
                    f"nodes: {total_nodes}  rels: {total_rels}  "
                    f"last refresh: {_ts()}  auto-refresh: {'on' if self._auto_refresh_enabled else 'off'}"
                )
                events.write(
                    f"[{_ts()}] schema OK — "
                    f"{len(node_records)} label(s), {len(rel_records)} type(s)"
                )

            except Exception as exc:
                status.update(f"[bold red]disconnected[/bold red]  {exc}")
                events.write(f"[{_ts()}] [red]schema refresh failed:[/red] {exc}")
            finally:
                self._inflight = False

        async def _run_cypher(self, query: str) -> None:
            res_tbl = self.query_one("#results", DataTable)
            events = self.query_one("#events", RichLog)

            events.write(f"[{_ts()}] [cyan]running:[/cyan] {_trunc(query, 80)}")
            try:
                async with self._make_driver() as driver:
                    records, summary, keys = await driver.execute_query(
                        query, database_="neo4j"
                    )

                # Rebuild results table with dynamic columns
                res_tbl.clear(columns=True)
                if keys:
                    res_tbl.add_columns(*keys)
                    for rec in records:
                        res_tbl.add_row(*[_trunc(rec[k]) for k in keys])

                elapsed = summary.result_available_after
                events.write(
                    f"[{_ts()}] [green]{len(records)} row(s)[/green] "
                    f"returned in {elapsed} ms"
                )
            except Exception as exc:
                events.write(f"[{_ts()}] [red]query error:[/red] {exc}")

    return GraphViewerApp


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def run_graph_viewer(
    neo4j_url: str = DEFAULT_NEO4J_URL,
    user: str = DEFAULT_NEO4J_USER,
    password: str = DEFAULT_NEO4J_PASSWORD,
    interval_seconds: float = DEFAULT_INTERVAL,
) -> int:
    app_cls = create_graph_viewer_app(neo4j_url, user, password, interval_seconds)
    app = app_cls()
    app.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Textual Neo4j graph viewer for LIARA",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--neo4j-url",
        default=DEFAULT_NEO4J_URL,
        help="Neo4j Bolt URL",
    )
    parser.add_argument(
        "--user",
        default=DEFAULT_NEO4J_USER,
        help="Neo4j username",
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_NEO4J_PASSWORD,
        help="Neo4j password",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="Schema auto-refresh interval in seconds",
    )
    args = parser.parse_args(argv)

    return run_graph_viewer(
        neo4j_url=args.neo4j_url,
        user=args.user,
        password=args.password,
        interval_seconds=args.interval,
    )


if __name__ == "__main__":
    raise SystemExit(main())
