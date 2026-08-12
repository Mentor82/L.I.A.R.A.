from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    argument: str


def parse_command(text: str) -> ParsedCommand | None:
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return None
    if " " in raw:
        left, right = raw.split(" ", 1)
        return ParsedCommand(name=left.lower(), argument=right.strip())
    return ParsedCommand(name=raw.lower(), argument="")


def help_text() -> str:
    return (
        "[bold]Commands[/bold]\n"
        "/help - Show command list\n"
        "/mode chat|stream - Set response mode\n"
        "/max-tokens <n> - Update max tokens\n"
        "/history [limit] - Show recent session history\n"
        "/health - API health\n"
        "/tools - List available tools\n"
        "/cache [stats|clear] - Inspect or clear local client cache\n"
        "/clear - Clear transcript\n"
        "/quit - Exit app"
    )
