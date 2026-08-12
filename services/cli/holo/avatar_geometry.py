"""Vector geometry definitions for the LIARA holo avatar."""

from __future__ import annotations

from dataclasses import dataclass


VecPoint = tuple[float, float]


@dataclass(frozen=True)
class Segment:
    """Simple 2D line segment in normalized coordinates (-1..1)."""

    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class VecLayer:
    """Neutral layer definition (style is applied at render time)."""

    name: str
    points: list[VecPoint]
    kind: str = "polyline"  # polyline | points | circle
    visible: bool = True
    opacity: float = 1.0
    glow: float = 0.5


def head_contour() -> list[Segment]:
    return [
        Segment(-0.55, -0.35, -0.62, -0.05),
        Segment(-0.62, -0.05, -0.62, 0.22),
        Segment(-0.62, 0.22, -0.48, 0.52),
        Segment(-0.48, 0.52, -0.18, 0.72),
        Segment(-0.18, 0.72, 0.18, 0.72),
        Segment(0.18, 0.72, 0.48, 0.52),
        Segment(0.48, 0.52, 0.62, 0.22),
        Segment(0.62, 0.22, 0.62, -0.05),
        Segment(0.62, -0.05, 0.55, -0.35),
        Segment(0.55, -0.35, 0.22, -0.70),
        Segment(0.22, -0.70, -0.22, -0.70),
        Segment(-0.22, -0.70, -0.55, -0.35),
    ]


def eyes() -> list[Segment]:
    return [
        Segment(-0.28, 0.18, -0.08, 0.22),
        Segment(-0.08, 0.22, 0.08, 0.22),
        Segment(0.08, 0.22, 0.28, 0.18),
        Segment(-0.08, 0.16, 0.08, 0.16),
    ]


def jaw_lines() -> list[Segment]:
    return [
        Segment(-0.32, -0.42, -0.15, -0.56),
        Segment(0.32, -0.42, 0.15, -0.56),
        Segment(-0.14, -0.56, 0.14, -0.56),
    ]


def ring_segments(steps: int = 36, radius_x: float = 0.9, radius_y: float = 0.32) -> list[Segment]:
    import math

    segments: list[Segment] = []
    for i in range(steps):
        a0 = (2 * math.pi * i) / steps
        a1 = (2 * math.pi * (i + 1)) / steps
        segments.append(
            Segment(
                radius_x * math.cos(a0),
                -0.02 + radius_y * math.sin(a0),
                radius_x * math.cos(a1),
                -0.02 + radius_y * math.sin(a1),
            )
        )
    return segments


def particle_seed_points() -> list[tuple[float, float]]:
    return [
        (-0.72, -0.58),
        (-0.48, 0.62),
        (0.46, 0.60),
        (0.70, -0.54),
        (0.0, 0.82),
        (0.0, -0.82),
    ]


def build_avatar_layers() -> list[VecLayer]:
    """Build avatar as neutral geometry layers (A/B/C/D)."""
    outline = head_contour()
    eye_lines = eyes()
    jaw = jaw_lines()
    ring = ring_segments(steps=32)

    head_outline_points: list[VecPoint] = [(seg.x0, seg.y0) for seg in outline]
    if outline:
        head_outline_points.append((outline[-1].x1, outline[-1].y1))

    eye_points: list[VecPoint] = []
    for seg in eye_lines:
        eye_points.extend([(seg.x0, seg.y0), (seg.x1, seg.y1)])

    jaw_points: list[VecPoint] = []
    for seg in jaw:
        jaw_points.extend([(seg.x0, seg.y0), (seg.x1, seg.y1)])

    ring_points: list[VecPoint] = [(seg.x0, seg.y0) for seg in ring]
    if ring:
        ring_points.append((ring[-1].x1, ring[-1].y1))

    shoulder_arc: list[VecPoint] = [(-0.78, -0.92), (-0.42, -0.82), (0.42, -0.82), (0.78, -0.92)]
    neck_left: list[VecPoint] = [(-0.22, -0.70), (-0.24, -0.86)]
    neck_right: list[VecPoint] = [(0.22, -0.70), (0.24, -0.86)]

    forehead_lines: list[VecPoint] = [(-0.22, 0.46), (0.0, 0.52), (0.22, 0.46)]
    nose_line: list[VecPoint] = [(0.0, 0.22), (0.0, -0.16)]
    cheek_left: list[VecPoint] = [(-0.42, 0.18), (-0.34, -0.10)]
    cheek_right: list[VecPoint] = [(0.42, 0.18), (0.34, -0.10)]

    hud_markers: list[VecPoint] = [(-0.84, 0.78), (0.84, 0.78), (-0.86, -0.76), (0.86, -0.76)]

    return [
        # Ebene A - Grundsilhouette
        VecLayer("head_outline", head_outline_points, kind="polyline", opacity=0.95, glow=0.75),
        VecLayer("neck_left", neck_left, kind="polyline", opacity=0.8, glow=0.45),
        VecLayer("neck_right", neck_right, kind="polyline", opacity=0.8, glow=0.45),
        VecLayer("shoulder_arc", shoulder_arc, kind="polyline", opacity=0.7, glow=0.4),
        # Ebene B - innere Konturen
        VecLayer("eye_lines", eye_points, kind="polyline", opacity=0.9, glow=0.7),
        VecLayer("nose_line", nose_line, kind="polyline", opacity=0.65, glow=0.4),
        VecLayer("cheek_left", cheek_left, kind="polyline", opacity=0.55, glow=0.3),
        VecLayer("cheek_right", cheek_right, kind="polyline", opacity=0.55, glow=0.3),
        VecLayer("forehead_lines", forehead_lines, kind="polyline", opacity=0.6, glow=0.35),
        VecLayer("jaw_line", jaw_points, kind="polyline", opacity=0.85, glow=0.6),
        # Ebene C - Holo Struktur
        VecLayer("halo_ring", ring_points, kind="polyline", opacity=0.75, glow=0.8),
        VecLayer("hud_markers", hud_markers, kind="points", opacity=0.75, glow=0.65),
        # Ebene D - Partikel
        VecLayer("particle_field", list(particle_seed_points()), kind="points", opacity=0.7, glow=0.55),
    ]
