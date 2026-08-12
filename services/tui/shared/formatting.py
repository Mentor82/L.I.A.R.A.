"""Formatting helpers shared by TUI apps."""

from __future__ import annotations


def status_style(status: str) -> str:
    if status == "healthy":
        return "[bold green]healthy[/bold green]"
    if status == "degraded":
        return "[yellow]degraded[/yellow]"
    if status == "unavailable":
        return "[dim]unavailable[/dim]"
    return "[dim]-[/dim]"


def bool_style(value: bool) -> str:
    return "[green]yes[/green]" if value else "[dim]no[/dim]"
