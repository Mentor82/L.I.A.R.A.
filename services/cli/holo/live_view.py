"""Rich Live integration for holo rendering."""

from __future__ import annotations

import time

from rich.align import Align
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .avatar_renderer import render_avatar
from .avatar_state import state_for_holo_mode


VALID_HOLO_MODES = {"core", "wire", "face", "scan"}


def run_holo_live(console: Console, duration_seconds: float = 5.0, mode: str = "core") -> int:
    mode = mode.lower()
    if mode not in VALID_HOLO_MODES:
        mode = "core"

    duration_seconds = max(0.5, min(30.0, duration_seconds))
    fps = 20
    frame_dt = 1.0 / fps

    state = state_for_holo_mode(mode)
    start = time.monotonic()
    tick = 0.0

    with Live(console=console, refresh_per_second=fps, transient=True) as live:
        while (time.monotonic() - start) < duration_seconds:
            width = 40
            height = 22
            panel = render_avatar(mode=mode, state=state, tick=tick, width=width, height=height)
            live.update(Align.right(panel, vertical="top"))
            tick += 0.25
            time.sleep(frame_dt)

    console.print(f"[dim]holo complete ({mode})[/dim]")
    return 0


def run_startup_header(
    console: Console,
    mode: str = "core",
    duration: float = 2.5,
) -> None:
    """Animated startup header: commands left, holo right.

    Uses transient=False so the last frame stays visible in the terminal
    and the REPL prompt appears below it.
    """
    mode = mode.lower() if mode in VALID_HOLO_MODES else "core"
    state = state_for_holo_mode(mode)
    fps = 20
    frame_dt = 1.0 / fps
    start = time.monotonic()
    tick = 0.0

    welcome = Text()
    welcome.append("Liara CLI REPL\n\n", style="bold white")
    welcome.append("/help    /history   /session\n", style="dim cyan")
    welcome.append("/mode    /tools     /status\n", style="dim cyan")
    welcome.append("/diag    /holo      /time\n", style="dim cyan")
    welcome.append("/search  /quit\n", style="dim cyan")

    with Live(console=console, refresh_per_second=fps, transient=False) as live:
        while (time.monotonic() - start) < duration:
            holo = render_avatar(mode=mode, state=state, tick=tick, width=38, height=12)

            grid = Table.grid(padding=0)
            grid.add_column("left", ratio=1)
            grid.add_column("right", width=40)
            grid.add_row(welcome, holo)

            outer = Panel(
                grid,
                title="[bold green]🚀 Liara[/bold green]",
                border_style="green",
            )
            live.update(outer)
            tick += 0.25
            time.sleep(frame_dt)
