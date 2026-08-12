#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unified TUI launcher for LIARA modules."""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
import uuid
from dataclasses import dataclass
from typing import Any

from services.cli.main import DEFAULT_BASE_URL, DEFAULT_HTTP_TIMEOUT, DEFAULT_USER_ID


@dataclass(frozen=True)
class LaunchTarget:
    """Represents one launchable TUI module."""

    key: str
    title: str
    description: str
    module: str


TARGETS: dict[str, LaunchTarget] = {
    "shell": LaunchTarget(
        key="1",
        title="Liara Shell",
        description="Prompt Toolkit REPL for chat + tools",
        module="services.tui.apps.liara_shell",
    ),
    "workers": LaunchTarget(
        key="2",
        title="Worker Monitor",
        description="Live backend status dashboard",
        module="services.tui.apps.worker_monitor",
    ),
    "memory": LaunchTarget(
        key="3",
        title="Memory Inspector",
        description="Session/history viewer (requires session-id)",
        module="services.tui.apps.memory_inspector",
    ),
    "admin": LaunchTarget(
        key="4",
        title="Admin Console",
        description="System overview: health, backends, tools",
        module="services.tui.apps.admin_console",
    ),
    "graph": LaunchTarget(
        key="5",
        title="Graph Viewer",
        description="Neo4j schema + Cypher viewer",
        module="services.tui.graph_viewer",
    ),
    "audit": LaunchTarget(
        key="6",
        title="Sys Audit TUI",
        description="Cross-domain audit timeline + signals",
        module="services.tui.sys_audit_tui",
    ),
    "osc": LaunchTarget(
        key="7",
        title="Latency Oscilloscope",
        description="Live timing waveforms (embed/retrieval/inference/total)",
        module="services.tui.apps.latency_oscilloscope",
    ),
}


def _load_textual_symbols() -> tuple[Any, ...]:
    """Load Textual classes lazily with an install hint if unavailable."""
    try:
        app_mod = importlib.import_module("textual.app")
        binding_mod = importlib.import_module("textual.binding")
        containers_mod = importlib.import_module("textual.containers")
        widgets_mod = importlib.import_module("textual.widgets")
    except ImportError as exc:
        raise RuntimeError(
            "textual is required for services.tui.launcher. "
            "Install it with: pip install textual"
        ) from exc

    App = getattr(app_mod, "App")
    Binding = getattr(binding_mod, "Binding")
    Vertical = getattr(containers_mod, "Vertical")
    Horizontal = getattr(containers_mod, "Horizontal")
    Header = getattr(widgets_mod, "Header")
    Footer = getattr(widgets_mod, "Footer")
    Static = getattr(widgets_mod, "Static")
    Input = getattr(widgets_mod, "Input")
    Button = getattr(widgets_mod, "Button")

    return App, Binding, Vertical, Horizontal, Header, Footer, Static, Input, Button


def _default_session_id() -> str:
    return f"session-{uuid.uuid4().hex[:8]}"


def _target_from_choice(choice: str) -> str:
    cleaned = (choice or "").strip().lower()
    if cleaned in TARGETS:
        return cleaned
    for name, target in TARGETS.items():
        if cleaned == target.key:
            return name
    raise ValueError(f"Unknown target '{choice}'. Use one of: {', '.join(TARGETS)}")


def _build_module_args(
    *,
    app_name: str,
    base_url: str,
    timeout: float,
    user_id: str,
    interval: float,
    session_id: str | None,
    neo4j_url: str,
    neo4j_user: str,
    neo4j_password: str,
    history_limit: int,
) -> list[str]:
    args: list[str] = []

    if app_name == "shell":
        args.extend(["--base-url", base_url, "--timeout", str(timeout), "--user-id", user_id])
        if session_id:
            args.extend(["--session-id", session_id])
        return args

    if app_name == "workers":
        return ["--base-url", base_url, "--timeout", str(timeout), "--interval", str(interval)]

    if app_name == "memory":
        if not session_id:
            raise ValueError("Memory Inspector requires --session-id")
        return [
            "--base-url",
            base_url,
            "--timeout",
            str(timeout),
            "--interval",
            str(interval),
            "--session-id",
            session_id,
            "--user-id",
            user_id,
            "--limit",
            str(history_limit),
        ]

    if app_name == "admin":
        return [
            "--base-url",
            base_url,
            "--timeout",
            str(timeout),
            "--interval",
            str(interval),
            "--user-id",
            user_id,
        ]

    if app_name == "graph":
        return [
            "--neo4j-url",
            neo4j_url,
            "--user",
            neo4j_user,
            "--password",
            neo4j_password,
            "--interval",
            str(interval),
        ]

    if app_name == "audit":
        return ["--scope", "project", "--interval", str(interval)]

    if app_name == "osc":
        return [
            "--source",
            "jsonl",
            "--jsonl",
            "logs/services/orchestrator/latency_scope.jsonl",
            "--poll",
            str(interval),
        ]

    raise ValueError(f"Unsupported target: {app_name}")


def _run_target(
    *,
    app_name: str,
    base_url: str,
    timeout: float,
    user_id: str,
    interval: float,
    session_id: str | None,
    neo4j_url: str,
    neo4j_user: str,
    neo4j_password: str,
    history_limit: int,
) -> int:
    target = TARGETS[app_name]
    module_args = _build_module_args(
        app_name=app_name,
        base_url=base_url,
        timeout=timeout,
        user_id=user_id,
        interval=interval,
        session_id=session_id,
        neo4j_url=neo4j_url,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password,
        history_limit=history_limit,
    )
    cmd = [sys.executable, "-m", target.module, *module_args]
    completed = subprocess.run(cmd, check=False)
    return int(completed.returncode)


def create_launcher_app(
    *,
    base_url: str,
    timeout: float,
    user_id: str,
    interval: float,
    default_session_id: str,
    neo4j_url: str,
    neo4j_user: str,
    neo4j_password: str,
    history_limit: int,
):
    """Create the Textual launcher app class."""
    App, Binding, Vertical, Horizontal, Header, Footer, Static, Input, Button = _load_textual_symbols()

    class LauncherApp(App):
        TITLE = "LIARA TUI Launcher"
        SUB_TITLE = "Start shell, monitors, and dashboards"

        CSS = """
        Screen {
            layout: vertical;
        }

        #body {
            height: 1fr;
            padding: 1 2;
        }

        #apps {
            height: auto;
            border: solid #3d4c63;
            padding: 1;
        }

        #apps Button {
            width: 1fr;
            margin: 0 0 1 0;
        }

        #memory_row {
            height: auto;
            border: solid #3d4c63;
            padding: 1;
            margin: 1 0 0 0;
            layout: horizontal;
        }

        #memory_label {
            width: 18;
            content-align: left middle;
        }

        #memory_session {
            width: 1fr;
        }

        #hint {
            height: auto;
            margin: 1 0 0 0;
            color: $text-muted;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("1", "launch_shell", "Shell"),
            Binding("2", "launch_workers", "Workers"),
            Binding("3", "launch_memory", "Memory"),
            Binding("4", "launch_admin", "Admin"),
            Binding("5", "launch_graph", "Graph"),
            Binding("6", "launch_audit", "Audit"),
        ]

        def compose(self):
            yield Header(show_clock=True)
            with Vertical(id="body"):
                yield Static("[bold cyan]Choose a TUI module[/bold cyan]", id="title")
                with Vertical(id="apps"):
                    for name in ("shell", "workers", "memory", "admin", "graph", "audit"):
                        target = TARGETS[name]
                        yield Button(
                            f"{target.key}) {target.title}  [dim]- {target.description}[/dim]",
                            id=f"app_{name}",
                        )
                with Horizontal(id="memory_row"):
                    yield Static("Memory Session ID", id="memory_label")
                    yield Input(value=default_session_id, id="memory_session")
                yield Static(
                    "Hotkeys: [bold]1..6[/bold] launch, [bold]q[/bold] quit",
                    id="hint",
                )
            yield Footer()

        def _session_id(self) -> str:
            value = self.query_one("#memory_session", Input).value.strip()
            return value or _default_session_id()

        def _launch(self, app_name: str) -> None:
            session_id = self._session_id() if app_name == "memory" else None
            self.exit({"app": app_name, "session_id": session_id})

        def on_button_pressed(self, event) -> None:
            button_id = event.button.id or ""
            if button_id.startswith("app_"):
                self._launch(button_id.replace("app_", "", 1))

        def action_launch_shell(self) -> None:
            self._launch("shell")

        def action_launch_workers(self) -> None:
            self._launch("workers")

        def action_launch_memory(self) -> None:
            self._launch("memory")

        def action_launch_admin(self) -> None:
            self._launch("admin")

        def action_launch_graph(self) -> None:
            self._launch("graph")

        def action_launch_audit(self) -> None:
            self._launch("audit")

    return LauncherApp


def run_launcher(
    *,
    base_url: str,
    timeout: float,
    user_id: str,
    interval: float,
    session_id: str | None,
    neo4j_url: str,
    neo4j_user: str,
    neo4j_password: str,
    history_limit: int,
) -> int:
    launcher_cls = create_launcher_app(
        base_url=base_url,
        timeout=timeout,
        user_id=user_id,
        interval=interval,
        default_session_id=session_id or _default_session_id(),
        neo4j_url=neo4j_url,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password,
        history_limit=history_limit,
    )
    app = launcher_cls()
    result = app.run()
    if not result:
        return 0

    selected_app = str(result.get("app") or "").strip().lower()
    selected_session = result.get("session_id")
    if not selected_app:
        return 0

    return _run_target(
        app_name=selected_app,
        base_url=base_url,
        timeout=timeout,
        user_id=user_id,
        interval=interval,
        session_id=selected_session,
        neo4j_url=neo4j_url,
        neo4j_user=neo4j_user,
        neo4j_password=neo4j_password,
        history_limit=history_limit,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="LIARA unified TUI launcher",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--app",
        choices=sorted(TARGETS.keys()),
        help="Directly launch one app without opening the launcher UI",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="liara-api base URL")
    parser.add_argument("--timeout", type=float, default=DEFAULT_HTTP_TIMEOUT, help="HTTP timeout (seconds)")
    parser.add_argument("--interval", type=float, default=5.0, help="Poll interval for monitor apps")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID, help="User id")
    parser.add_argument("--session-id", help="Session id for shell/memory (required for --app memory)")
    parser.add_argument("--history-limit", type=int, default=100, help="History row limit for memory inspector")

    parser.add_argument("--neo4j-url", default="bolt://127.0.0.1:7688", help="Neo4j Bolt URL")
    parser.add_argument("--neo4j-user", default="neo4j", help="Neo4j username")
    parser.add_argument("--neo4j-password", default="liara2026", help="Neo4j password")

    args = parser.parse_args(argv)

    if args.app:
        app_name = _target_from_choice(args.app)
        return _run_target(
            app_name=app_name,
            base_url=args.base_url,
            timeout=args.timeout,
            user_id=args.user_id,
            interval=args.interval,
            session_id=args.session_id,
            neo4j_url=args.neo4j_url,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            history_limit=args.history_limit,
        )

    return run_launcher(
        base_url=args.base_url,
        timeout=args.timeout,
        user_id=args.user_id,
        interval=args.interval,
        session_id=args.session_id,
        neo4j_url=args.neo4j_url,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        history_limit=args.history_limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
