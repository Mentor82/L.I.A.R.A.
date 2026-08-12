from __future__ import annotations

from dataclasses import dataclass
import shlex


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    argument: str


@dataclass(frozen=True)
class SysInvocation:
    command: str
    args: list[str]
    stdin_text: str | None = None


def parse_sys_invocation(argument: str) -> SysInvocation:
    """Parse a direct argv-style /sys request without introducing a shell."""
    raw = (argument or "").strip()
    if not raw:
        raise ValueError("Usage: /sys <command> [args...] [--stdin <text>]")

    header, newline, body = raw.partition("\n")
    stdin_text: str | None = body if newline else None
    for marker in (" --stdin ", " <<< "):
        if marker in header:
            header, inline_stdin = header.split(marker, 1)
            if stdin_text is not None:
                raise ValueError("Provide stdin either inline or on following lines, not both.")
            try:
                parsed_stdin = shlex.split(inline_stdin, posix=True)
            except ValueError as exc:
                raise ValueError(f"Invalid stdin quoting: {exc}") from exc
            stdin_text = parsed_stdin[0] if len(parsed_stdin) == 1 else inline_stdin
            break

    try:
        argv = shlex.split(header, posix=True)
    except ValueError as exc:
        raise ValueError(f"Invalid /sys quoting: {exc}") from exc
    if not argv:
        raise ValueError("Usage: /sys <command> [args...]")
    return SysInvocation(command=argv[0], args=argv[1:], stdin_text=stdin_text)


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
        "/sys <command> [args] - Execute a structured policy-gated WSL command\n"
        "/sys tee <path> followed by Shift+Enter content - Write with verified stdin\n"
        "/cache [stats|clear] - Inspect or clear local client cache\n"
        "/clear - Clear transcript\n"
        "/quit - Exit app"
    )
