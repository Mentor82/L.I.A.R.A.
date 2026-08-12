"""Raster renderer that converts neutral vector layers into Rich renderables."""

from __future__ import annotations

import math
from typing import Iterable

from rich.panel import Panel
from rich.text import Text

from .avatar_geometry import VecLayer, build_avatar_layers
from .avatar_state import AvatarState


_CANVAS_CHARS = " .:-=+*#%@"


def _to_grid(x: float, y: float, width: int, height: int) -> tuple[int, int]:
    gx = int((x + 1.0) * 0.5 * (width - 1))
    gy = int((1.0 - (y + 1.0) * 0.5) * (height - 1))
    return gx, gy


def _plot(grid: list[list[str]], x: int, y: int, char: str) -> None:
    if 0 <= y < len(grid) and 0 <= x < len(grid[0]):
        grid[y][x] = char


def _draw_line(grid: list[list[str]], p0: tuple[int, int], p1: tuple[int, int], char: str) -> None:
    x0, y0 = p0
    x1, y1 = p1

    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy

    while True:
        _plot(grid, x0, y0, char)
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _char_for_layer(layer: VecLayer, state: AvatarState) -> str:
    if layer.kind == "points":
        return "*" if state.energy > 0.5 else "."

    if state.state == "error":
        return "!"
    if state.state == "warning":
        return "+"
    if state.state in {"thinking", "tool_active"}:
        return "#"
    if layer.name.startswith("eye"):
        return "="
    return "-"


def _modulate_point(x: float, y: float, tick: float, state: AvatarState, layer_name: str) -> tuple[float, float]:
    bob = 0.015 * math.sin(tick * 1.2)
    jitter = 0.0
    if state.state == "thinking":
        jitter = 0.01 * math.sin(tick * 2.6 + y * 4.0)
    elif state.state == "responding":
        jitter = 0.02 * state.voice_level * math.sin(tick * 5.2 + x * 5.0)
    elif state.state == "error":
        jitter = 0.03 * math.sin(tick * 8.0 + (x + y) * 9.0)

    if layer_name == "halo_ring":
        # slow orbit for tool/thinking modes
        speed = 0.2 + 0.5 * state.focus
        angle = tick * speed
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        xr = x * cos_a - y * sin_a
        yr = x * sin_a + y * cos_a
        x, y = xr, yr

    return x + jitter, y + bob + jitter * 0.4


def _draw_layer(grid: list[list[str]], layer: VecLayer, width: int, height: int, tick: float, state: AvatarState) -> None:
    if not layer.visible or not layer.points:
        return

    char = _char_for_layer(layer, state)
    points = [_modulate_point(px, py, tick, state, layer.name) for (px, py) in layer.points]

    if layer.kind == "points":
        for px, py in points:
            gx, gy = _to_grid(px, py, width, height)
            _plot(grid, gx, gy, char)
            if state.intensity > 0.7:
                _plot(grid, gx + 1, gy, ".")
        return

    if len(points) < 2:
        return

    for i in range(len(points) - 1):
        p0 = _to_grid(points[i][0], points[i][1], width, height)
        p1 = _to_grid(points[i + 1][0], points[i + 1][1], width, height)
        _draw_line(grid, p0, p1, char)


def _scanline_overlay(grid: list[list[str]], tick: float) -> None:
    height = len(grid)
    width = len(grid[0]) if height else 0
    if height == 0 or width == 0:
        return
    y = int((tick * 6) % height)
    for x in range(width):
        if grid[y][x] == " ":
            grid[y][x] = "."


def _net_overlay(grid: list[list[str]], tick: float, enabled: bool) -> None:
    if not enabled:
        return
    height = len(grid)
    width = len(grid[0]) if height else 0
    if height < 2 or width < 2:
        return
    stride = 5
    drift = int((tick * 2) % stride)
    for y in range(drift, height, stride):
        for x in range(0, width, stride):
            if grid[y][x] == " ":
                grid[y][x] = ":"


def render_avatar(mode: str, state: AvatarState, tick: float, width: int, height: int) -> Panel:
    grid = [[" " for _ in range(width)] for _ in range(height)]

    layers = build_avatar_layers()

    # Hide layers depending on selected visual mode
    for layer in layers:
        if mode == "core" and layer.name in {"halo_ring", "hud_markers"}:
            layer.visible = False
        if mode == "wire" and layer.name in {"eye_lines", "jaw_line", "nose_line", "cheek_left", "cheek_right"}:
            layer.visible = False
        if mode == "face" and layer.name == "hud_markers":
            layer.visible = False

    for layer in layers:
        _draw_layer(grid, layer, width, height, tick, state)

    _net_overlay(grid, tick, enabled=mode in {"wire", "scan", "face"})
    if mode == "scan" or state.state in {"tool_active", "warning", "error"}:
        _scanline_overlay(grid, tick)

    body_lines = ["".join(row) for row in grid]
    glow = min(len(_CANVAS_CHARS) - 1, int(state.intensity * (len(_CANVAS_CHARS) - 1)))
    style = "bright_cyan" if glow >= 6 else "cyan"
    text = Text("\n".join(body_lines), style=style)

    title = {
        "core": "LIARA HOLO CORE",
        "wire": "LIARA HOLO WIRE",
        "face": "LIARA HOLO FACE",
        "scan": "LIARA HOLO SCAN",
    }.get(mode, "LIARA HOLO")

    subtitle = f"STATE:{state.state}  E:{state.energy:.2f}  F:{state.focus:.2f}"
    if state.tool_name:
        subtitle += f"  TOOL:{state.tool_name}"

    return Panel(text, title=f"[bold blue]{title}[/bold blue]", subtitle=subtitle, border_style="bright_blue")
