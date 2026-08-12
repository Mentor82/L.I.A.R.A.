"""Built-in: Generate simple PNG charts and return chat artifact metadata."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore

from services.shared.sandboxing import get_global_sandbox_root, resolve_sandbox_root

from ..base import Tool

logger = logging.getLogger(__name__)


def _sanitize_filename(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or "chart").strip())
    safe = safe.strip("._")
    return safe or "chart"


def _clamp_int(value: Any, minimum: int, maximum: int, default: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return max(minimum, min(maximum, parsed))


def _parse_float_list(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        out: list[float] = []
        for item in value:
            out.append(float(item))
        return out
    return None


def _fallback_series_from_query(query: str, length: int = 6) -> list[float]:
    text = (query or "").strip() or "liara"
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    values: list[float] = []
    for index in range(length):
        raw = digest[index] if index < len(digest) else 0
        values.append(float(1 + (raw % 10)))
    return values


class PlotChartTool(Tool):
    """Generate simple chart PNGs (line/bar) and emit artifacts for chat rendering."""

    def __init__(self, allowed_root: Path | None = None) -> None:
        self._root = (allowed_root or get_global_sandbox_root()).resolve()

    @property
    def name(self) -> str:
        return "plot_chart"

    @property
    def description(self) -> str:
        return "Generate a simple line or bar chart PNG from numeric values"

    @property
    def required_parameters(self) -> list[str]:
        return []

    @property
    def optional_parameters(self) -> list[str]:
        return [
            "query",
            "x_values",
            "y_values",
            "chart_type",
            "title",
            "width",
            "height",
            "session_id",
            "sandbox_root",
            "filename",
        ]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self._validate_parameters(**kwargs)
        logger.debug(f"PlotChartTool.execute() started for session {kwargs.get('session_id')}")

        query = str(kwargs.get("query") or "")
        chart_type = str(kwargs.get("chart_type") or "line").strip().lower()
        title = str(kwargs.get("title") or "LIARA Chart").strip() or "LIARA Chart"
        session_id = _sanitize_filename(str(kwargs.get("session_id") or "session"))
        filename_hint = _sanitize_filename(str(kwargs.get("filename") or title or "chart"))
        width = _clamp_int(kwargs.get("width"), minimum=320, maximum=1920, default=960)
        height = _clamp_int(kwargs.get("height"), minimum=240, maximum=1080, default=540)

        if chart_type not in {"line", "bar"}:
            return self.failure("Invalid chart_type. Use 'line' or 'bar'.")

        y_values = _parse_float_list(kwargs.get("y_values"))
        if y_values is None:
            y_values = _fallback_series_from_query(query)
        if not y_values:
            return self.failure("No data points available for plotting.")

        x_values = _parse_float_list(kwargs.get("x_values"))
        if x_values is None:
            x_values = [float(i + 1) for i in range(len(y_values))]

        if len(x_values) != len(y_values):
            return self.failure("x_values and y_values must have the same length.")
        if len(y_values) < 2:
            return self.failure("At least 2 points are required to draw a chart.")

        try:
            effective_root = resolve_sandbox_root(kwargs.get("sandbox_root"), self._root)
        except ValueError as exc:
            return self.failure(str(exc))

        output_dir = effective_root / ".liara_artifacts" / session_id
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{filename_hint}_{uuid4().hex[:8]}.png"
        output_path = output_dir / filename

        try:
            image = Image.new("RGB", (width, height), color=(248, 250, 252))
            draw = ImageDraw.Draw(image)

            margin_left = 64
            margin_right = 24
            margin_top = 52
            margin_bottom = 52

            plot_left = margin_left
            plot_top = margin_top
            plot_right = width - margin_right
            plot_bottom = height - margin_bottom
            plot_width = max(10, plot_right - plot_left)
            plot_height = max(10, plot_bottom - plot_top)

            draw.rectangle([plot_left, plot_top, plot_right, plot_bottom], outline=(148, 163, 184), width=1)
            draw.text((plot_left, 16), title[:80], fill=(15, 23, 42))

            min_x = min(x_values)
            max_x = max(x_values)
            min_y = min(y_values)
            max_y = max(y_values)
            if min_x == max_x:
                max_x = min_x + 1.0
            if min_y == max_y:
                max_y = min_y + 1.0

            def map_x(value: float) -> int:
                normalized = (value - min_x) / (max_x - min_x)
                return int(plot_left + normalized * plot_width)

            def map_y(value: float) -> int:
                normalized = (value - min_y) / (max_y - min_y)
                return int(plot_bottom - normalized * plot_height)

            if chart_type == "line":
                points = [(map_x(x), map_y(y)) for x, y in zip(x_values, y_values)]
                draw.line(points, fill=(37, 99, 235), width=3)
                for px, py in points:
                    draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill=(30, 64, 175))
            else:
                count = len(x_values)
                bar_gap = 8
                bar_width = max(4, int((plot_width - bar_gap * (count + 1)) / count))
                for index, y in enumerate(y_values):
                    x_start = plot_left + bar_gap + index * (bar_width + bar_gap)
                    x_end = x_start + bar_width
                    y_top = map_y(y)
                    draw.rectangle([x_start, y_top, x_end, plot_bottom], fill=(14, 116, 144), outline=(15, 23, 42))

            image.save(output_path, format="PNG")
            file_size_kb = output_path.stat().st_size / 1024
            logger.info(f"Chart artifact generated: {output_path.name} ({file_size_kb:.1f} KB)")
        except Exception as exc:
            logger.error(f"Chart generation failed: {exc}")
            return self.failure(f"Failed to generate chart image: {exc}")

        relative_path = output_path.relative_to(effective_root).as_posix()
        artifact = {
            "kind": "image",
            "mime_type": "image/png",
            "title": title,
            "url": None,
            "width": width,
            "height": height,
            "source_tool": self.name,
            "metadata": {
                "stored_path": relative_path,
                "session_id": session_id,
                "chart_type": chart_type,
                "point_count": len(y_values),
            },
        }

        logger.debug(f"Returning chart artifact: kind=image, title={title}, type={chart_type}, points={len(y_values)}")
        return self.success(
            {
                "chart_type": chart_type,
                "title": title,
                "path": relative_path,
                "artifacts": [artifact],
            },
            {
                "sandbox_root": str(effective_root),
            },
        )
