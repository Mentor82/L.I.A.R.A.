from __future__ import annotations

from difflib import get_close_matches

from rich.panel import Panel
from rich.table import Table

from .constants import KNOWN_COMMANDS, console
from .state import ShellState


def print_help() -> None:
    help_text = """
[bold cyan]Liara Shell Commands[/bold cyan]

[bold]Chat:[/bold]
  <message>          Send message to LLM
  /mode chat|stream  Switch mode

[bold]Info:[/bold]
  /status            Show runtime settings
  /show-config       Alias for /status
  /health            API + backend health
  /history [limit]   Show session history (default: 20)
  /context           Show loaded context
  /session           Show session info (persisted in ~/.liara/session.json)
  /session new       Create and switch to a new session
  /session set <id>  Switch to a specific session id

[bold]Config:[/bold]
  /set user <id>           Update active user id
  /set timeout <seconds>   Update HTTP timeout
  /set base-url <url>      Update API base URL
  /provider [show|set|reset]  Runtime provider profile in shell
  /model [show|set|reset]     Runtime model profile in shell
  /max-tokens <n>          Update generation max tokens

[bold]Tools:[/bold]
  /tools             List tools
  /tools <name>      Tool details
  /sys <command> [args...]  Invoke the canonical sys tool endpoint
  /diag              Diagnose Ollama/model setup

[bold]Input:[/bold]
    [triple-quote]     Enter multiline mode (end with triple-quote on its own line)
  /paste             Alias for multiline mode
  /clear, /cls       Clear terminal output
  /quit              Exit shell
"""
    console.print(Panel(help_text, title="[bold green]Help[/bold green]", border_style="green"))


def print_status(state: ShellState) -> None:
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="bold dim", min_width=14)
    table.add_column("Value", style="white")
    table.add_row("Base URL", state.base_url)
    table.add_row("Session", state.session_id)
    table.add_row("User", state.user_id)
    table.add_row("Mode", state.mode)
    table.add_row("Provider", state.preferred_provider or "-")
    table.add_row("Model", state.preferred_model or "-")
    table.add_row("Max Tokens", str(state.max_tokens))
    table.add_row("Timeout", f"{state.timeout:.1f}s")
    console.print(Panel(table, title="[bold cyan]Shell Status[/bold cyan]", border_style="cyan"))


def print_unknown_command_error(message: str) -> None:
    command = message.split(" ", 1)[0].strip().lower()
    suggestion = get_close_matches(command, list(KNOWN_COMMANDS.keys()), n=1, cutoff=0.6)
    console.print(f"[red]Unknown command:[/red] {command}")
    if suggestion:
        console.print(f"[yellow]Did you mean {suggestion[0]} ?[/yellow]")


def print_shell_header(state: ShellState) -> None:
    console.print(
        Panel(
            f"Liara Shell\n"
            f"[dim]Session:[/dim] [cyan]{state.session_id}[/cyan]\n"
            f"[dim]User:[/dim] [cyan]{state.user_id}[/cyan]  "
            f"[dim]Mode:[/dim] [yellow]{state.mode}[/yellow]  "
            f"[dim]Max Tokens:[/dim] [magenta]{state.max_tokens}[/magenta]\n"
            f"[dim]Provider:[/dim] {state.preferred_provider or '-'}  "
            f"[dim]Model:[/dim] {state.preferred_model or '-'}\n"
            f"[dim]Base URL:[/dim] {state.base_url}  "
            f"[dim]Timeout:[/dim] {state.timeout:.1f}s\n"
            f"[dim]Type /help for commands[/dim]",
            title="[bold green]LIARA[/bold green]",
            border_style="green",
        )
    )


def print_state_change(label: str, value: str) -> None:
    console.print(f"[yellow]{label} -> {value}[/yellow]")
