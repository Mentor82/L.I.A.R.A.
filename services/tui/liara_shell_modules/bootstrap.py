from __future__ import annotations

import json
import os
from urllib.parse import urlparse

import httpx
from rich.panel import Panel

from .constants import LIARA_DIR, SESSION_FILE, console


def load_session_id() -> str | None:
    """Load the last-used session ID from ~/.liara/session.json."""
    try:
        with open(SESSION_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
            sid = data.get("session_id")
            return str(sid) if sid else None
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def save_session_id(session_id: str) -> None:
    """Persist the active session ID to ~/.liara/session.json."""
    try:
        os.makedirs(LIARA_DIR, exist_ok=True)
        with open(SESSION_FILE, "w", encoding="utf-8") as fh:
            json.dump({"session_id": session_id}, fh)
    except OSError:
        pass


def uvicorn_hint_from_base_url(base_url: str) -> str:
    """Build a start command hint from the configured API URL."""
    parsed = urlparse(base_url)
    host = os.getenv("LIARA_API_BIND_HOST", "0.0.0.0")
    port = parsed.port or 8010
    return f"python -m uvicorn services.api.app:app --host {host} --port {port}"


def startup_preflight(base_url: str, timeout: float) -> bool:
    """Check API availability once and print a startup hint when offline."""
    probe_timeout = max(2.0, min(timeout, 5.0))
    try:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=probe_timeout) as client:
            response = client.get("/health")
            response.raise_for_status()
        return True
    except Exception as exc:
        hint = uvicorn_hint_from_base_url(base_url)
        console.print(
            Panel(
                f"[yellow]API seems offline:[/yellow] {exc}\n"
                f"[dim]Try starting it with:[/dim]\n[bold]{hint}[/bold]",
                title="[bold yellow]Startup Check[/bold yellow]",
                border_style="yellow",
            )
        )
        return False
