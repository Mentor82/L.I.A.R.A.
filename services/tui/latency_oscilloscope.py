#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Digital latency oscilloscope in Textual UI.

Shows live phase timings as human-friendly charts:
- total latency
- embed latency
- retrieval latency
- inference latency

Data sources:
- demo: synthetic timings (for quick visual checks)
- jsonl: tail a JSONL file with timing payloads
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Deque, Optional

from services.tui.shared import load_textual_symbols


@dataclass
class TimingSample:
    ts: float
    t_total: float
    t_embed: float
    t_retrieval: float
    t_inference: float
    device_embed: str
    model: str


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _normalize_to_seconds(value: Any, *, assume_ms_over: float = 100.0) -> float:
    v = _safe_float(value)
    if v <= 0.0:
        return 0.0
    if v > assume_ms_over:
        return v / 1000.0
    return v


def _extract_from_record(row: dict[str, Any]) -> Optional[TimingSample]:
    # Preferred direct schema
    if any(k in row for k in ("t_total", "t_embed", "t_retrieval", "t_inference")):
        t_total = _normalize_to_seconds(row.get("t_total", 0.0))
        t_embed = _normalize_to_seconds(row.get("t_embed", 0.0))
        t_ret = _normalize_to_seconds(row.get("t_retrieval", 0.0))
        t_inf = _normalize_to_seconds(row.get("t_inference", 0.0))
        if t_total <= 0:
            t_total = t_embed + t_ret + t_inf
        return TimingSample(
            ts=float(row.get("ts") or time.time()),
            t_total=t_total,
            t_embed=t_embed,
            t_retrieval=t_ret,
            t_inference=t_inf,
            device_embed=str(row.get("device_embed") or "unknown"),
            model=str(row.get("model") or "unknown"),
        )

    # Orchestrator-style metadata fallback
    md = row.get("metadata") or {}
    timings_ms = md.get("timings_ms") or {}
    if not timings_ms:
        return None

    # Map LIARA orchestrator timings to phase buckets.
    t_total = _normalize_to_seconds(timings_ms.get("total", 0.0))
    t_embed = _normalize_to_seconds(
        timings_ms.get("embedding_generation", 0.0)
        or timings_ms.get("embedding_total", 0.0)
    )
    t_ret = _normalize_to_seconds(
        timings_ms.get("tool_execution", 0.0)
        or timings_ms.get("retrieval_total", 0.0)
    )
    t_inf = _normalize_to_seconds(
        timings_ms.get("llm_generation_total", 0.0)
        or timings_ms.get("llm_total", 0.0)
    )

    if t_total <= 0:
        t_total = t_embed + t_ret + t_inf
    if t_total <= 0:
        return None

    return TimingSample(
        ts=float(row.get("timestamp") or time.time()),
        t_total=t_total,
        t_embed=t_embed,
        t_retrieval=t_ret,
        t_inference=t_inf,
        device_embed=str(md.get("device_embed") or "unknown"),
        model=str(row.get("llm_model") or md.get("model") or "unknown"),
    )


def _read_jsonl_tail(path: Path, *, lines: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        all_lines = fh.readlines()
    if lines <= 0:
        return all_lines
    return all_lines[-lines:]


def _render_wave(values: list[float], *, width: int, height: int, y_max: float, title: str) -> str:
    if width < 10:
        width = 10
    if height < 4:
        height = 4
    y_max = max(0.001, y_max)

    tail = values[-width:]
    if len(tail) < width:
        tail = ([0.0] * (width - len(tail))) + tail

    grid = [[" " for _ in range(width)] for _ in range(height)]

    for x, v in enumerate(tail):
        norm = max(0.0, min(1.0, v / y_max))
        row = int(round((1.0 - norm) * (height - 1)))
        grid[row][x] = "*"

    lines: list[str] = []
    lines.append(f"{title} (max={y_max:.2f}s)")
    for r, line_cells in enumerate(grid):
        if r == 0:
            label = f"{y_max:>6.2f}"
        elif r == height - 1:
            label = f"{0.0:>6.2f}"
        else:
            label = "      "
        lines.append(f"{label} |{''.join(line_cells)}|")
    lines.append("       +" + ("-" * width) + "+")
    return "\n".join(lines)


def _render_phase_bar(sample: TimingSample, *, width: int = 50) -> str:
    total = max(sample.t_total, 0.0001)

    def seg(v: float) -> int:
        return max(0, int(round((v / total) * width)))

    n_embed = seg(sample.t_embed)
    n_ret = seg(sample.t_retrieval)
    n_inf = seg(sample.t_inference)

    # Fit exactly to width
    s = n_embed + n_ret + n_inf
    if s < width:
        n_inf += width - s
    elif s > width:
        n_inf = max(0, n_inf - (s - width))

    bar = ("E" * n_embed) + ("R" * n_ret) + ("I" * n_inf)
    bar = bar[:width].ljust(width)
    return (
        f"Phase mix |{bar}|\n"
        f"E={sample.t_embed:.2f}s  R={sample.t_retrieval:.2f}s  I={sample.t_inference:.2f}s  "
        f"Total={sample.t_total:.2f}s"
    )


def _moving_average(values: list[float], window: int) -> float:
    if not values:
        return 0.0
    w = max(1, window)
    tail = values[-w:]
    return sum(tail) / len(tail)


def _mean_std(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, 0.0
    var = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return mean, var ** 0.5


def _is_spike(values: list[float], z_score: float, min_samples: int) -> tuple[bool, float, float]:
    if len(values) < max(3, min_samples):
        return False, 0.0, 0.0
    base = values[:-1]
    latest = values[-1]
    mean, std = _mean_std(base)
    if std <= 1e-9:
        return False, mean, std
    return latest > (mean + (z_score * std)), mean, std


def create_latency_oscilloscope_app(
    *,
    source: str,
    jsonl_path: Path,
    max_points: int,
    poll_interval: float,
    chart_height: int,
    chart_width: int,
    max_latency: float,
    ma_window: int,
    spike_z: float,
    spike_min_samples: int,
    alert_total: float,
    alert_embed: float,
    alert_retrieval: float,
    alert_inference: float,
):
    app_mod, binding_mod, containers_mod, widgets_mod = load_textual_symbols("services.tui.launcher")
    App = getattr(app_mod, "App")
    Binding = getattr(binding_mod, "Binding")
    Vertical = getattr(containers_mod, "Vertical")
    Horizontal = getattr(containers_mod, "Horizontal")
    Header = getattr(widgets_mod, "Header")
    Footer = getattr(widgets_mod, "Footer")
    Static = getattr(widgets_mod, "Static")

    class LatencyOscilloscopeApp(App):
        TITLE = "LIARA Latency Oscilloscope"
        SUB_TITLE = "Live phase timing monitor"

        CSS = """
        Screen {
            layout: vertical;
        }

        #main {
            height: 1fr;
            padding: 1 1;
        }

        #top {
            height: auto;
            border: solid #3d4c63;
            padding: 1;
        }

        #charts {
            height: 1fr;
            border: solid #3d4c63;
            padding: 1;
        }

        #left, #right {
            width: 1fr;
            height: 100%;
        }

        .panel {
            height: 1fr;
            border: solid #2b374a;
            margin: 0 1 1 0;
            padding: 0 1;
        }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("r", "refresh", "Refresh"),
            Binding("space", "pause", "Pause"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.source = source
            self.jsonl_path = jsonl_path
            self.max_points = max(20, max_points)
            self.poll_interval = max(0.2, poll_interval)
            self.chart_height = max(4, chart_height)
            self.chart_width = max(20, chart_width)
            self.max_latency = max(0.5, max_latency)
            self.ma_window = max(2, ma_window)
            self.spike_z = max(1.0, spike_z)
            self.spike_min_samples = max(4, spike_min_samples)
            self.alert_total = max(0.0, alert_total)
            self.alert_embed = max(0.0, alert_embed)
            self.alert_retrieval = max(0.0, alert_retrieval)
            self.alert_inference = max(0.0, alert_inference)
            self.paused = False
            self.samples: Deque[TimingSample] = deque(maxlen=self.max_points)
            self._file_pos = 0

        def compose(self):
            yield Header(show_clock=True)
            with Vertical(id="main"):
                yield Static("", id="top")
                with Horizontal(id="charts"):
                    with Vertical(id="left"):
                        yield Static("", id="wave_total", classes="panel")
                        yield Static("", id="wave_inference", classes="panel")
                    with Vertical(id="right"):
                        yield Static("", id="wave_embed", classes="panel")
                        yield Static("", id="wave_retrieval", classes="panel")
                yield Static("", id="phase_mix")
            yield Footer()

        def on_mount(self) -> None:
            if self.source == "jsonl":
                if self.jsonl_path.exists():
                    self._load_initial_tail()
                    self._file_pos = self.jsonl_path.stat().st_size
                else:
                    self._file_pos = 0
            self.set_interval(self.poll_interval, self._tick)
            self.call_later(self._render)

        def action_pause(self) -> None:
            self.paused = not self.paused
            self._render()

        def action_refresh(self) -> None:
            self._ingest()
            self._render()

        def _load_initial_tail(self) -> None:
            for line in _read_jsonl_tail(self.jsonl_path, lines=min(200, self.max_points * 2)):
                self._try_add_from_line(line)

        def _tick(self) -> None:
            if self.paused:
                return
            self._ingest()
            self._render()

        def _ingest(self) -> None:
            if self.source == "demo":
                self._add_demo_sample()
                return
            if self.source == "jsonl":
                self._tail_jsonl()

        def _add_demo_sample(self) -> None:
            t_embed = random.uniform(0.4, 1.6)
            t_ret = random.uniform(0.1, 0.8)
            t_inf = random.uniform(8.0, 16.0)
            t_total = t_embed + t_ret + t_inf + random.uniform(0.2, 1.0)
            self.samples.append(
                TimingSample(
                    ts=time.time(),
                    t_total=t_total,
                    t_embed=t_embed,
                    t_retrieval=t_ret,
                    t_inference=t_inf,
                    device_embed=random.choice(["NPU", "CPU", "GPU"]),
                    model=random.choice(["qwen-1.5b", "qwen-7b", "llama-3.2-3b"]),
                )
            )

        def _tail_jsonl(self) -> None:
            if not self.jsonl_path.exists():
                return
            with self.jsonl_path.open("r", encoding="utf-8", errors="replace") as fh:
                fh.seek(self._file_pos)
                for line in fh:
                    self._try_add_from_line(line)
                self._file_pos = fh.tell()

        def _try_add_from_line(self, line: str) -> None:
            line = line.strip()
            if not line:
                return
            try:
                row = json.loads(line)
            except Exception:
                return
            sample = _extract_from_record(row)
            if sample is not None:
                self.samples.append(sample)

        def _render(self) -> None:
            top = self.query_one("#top", Static)
            wave_total = self.query_one("#wave_total", Static)
            wave_inf = self.query_one("#wave_inference", Static)
            wave_emb = self.query_one("#wave_embed", Static)
            wave_ret = self.query_one("#wave_retrieval", Static)
            phase_mix = self.query_one("#phase_mix", Static)

            if not self.samples:
                top.update(
                    "No samples yet.\n"
                    f"source={self.source} jsonl={self.jsonl_path} paused={self.paused}\n"
                    "Press r to refresh, space to pause, q to quit."
                )
                wave_total.update("")
                wave_inf.update("")
                wave_emb.update("")
                wave_ret.update("")
                phase_mix.update("")
                return

            latest = self.samples[-1]
            totals = [s.t_total for s in self.samples]
            embeds = [s.t_embed for s in self.samples]
            rets = [s.t_retrieval for s in self.samples]
            infs = [s.t_inference for s in self.samples]

            avg_total = sum(totals) / len(totals)
            p95_idx = max(0, int(0.95 * len(self.samples)) - 1)
            p95_total = sorted(totals)[p95_idx]

            ma_total = _moving_average(totals, self.ma_window)
            ma_embed = _moving_average(embeds, self.ma_window)
            ma_ret = _moving_average(rets, self.ma_window)
            ma_inf = _moving_average(infs, self.ma_window)

            spike_total, mean_total, std_total = _is_spike(totals, self.spike_z, self.spike_min_samples)
            spike_embed, _, _ = _is_spike(embeds, self.spike_z, self.spike_min_samples)
            spike_ret, _, _ = _is_spike(rets, self.spike_z, self.spike_min_samples)
            spike_inf, _, _ = _is_spike(infs, self.spike_z, self.spike_min_samples)

            alerts: list[str] = []
            if latest.t_total >= self.alert_total > 0:
                alerts.append(f"TOTAL>{self.alert_total:.1f}s")
            if latest.t_embed >= self.alert_embed > 0:
                alerts.append(f"EMBED>{self.alert_embed:.1f}s")
            if latest.t_retrieval >= self.alert_retrieval > 0:
                alerts.append(f"RETR>{self.alert_retrieval:.1f}s")
            if latest.t_inference >= self.alert_inference > 0:
                alerts.append(f"INFER>{self.alert_inference:.1f}s")
            if spike_total:
                alerts.append("SPIKE_TOTAL")
            if spike_embed:
                alerts.append("SPIKE_EMBED")
            if spike_ret:
                alerts.append("SPIKE_RETR")
            if spike_inf:
                alerts.append("SPIKE_INFER")

            alert_line = "OK" if not alerts else ("ALERT: " + ", ".join(alerts))

            top.update(
                f"source={self.source}  paused={self.paused}  samples={len(self.samples)}  "
                f"device_embed={latest.device_embed}  model={latest.model}\n"
                f"latest_total={latest.t_total:.2f}s  avg_total={avg_total:.2f}s  p95_total={p95_total:.2f}s  "
                f"ma({self.ma_window})={ma_total:.2f}s\n"
                f"ma_embed={ma_embed:.2f}s  ma_ret={ma_ret:.2f}s  ma_inf={ma_inf:.2f}s  "
                f"spike_base(mean={mean_total:.2f},std={std_total:.2f})\n"
                f"{alert_line}"
            )

            y_max = max(
                self.max_latency,
                max(s.t_total for s in self.samples),
                max(s.t_inference for s in self.samples),
                max(s.t_embed for s in self.samples),
                max(s.t_retrieval for s in self.samples),
            )

            wave_total.update(
                _render_wave(
                    totals,
                    width=self.chart_width,
                    height=self.chart_height,
                    y_max=y_max,
                    title="Total latency",
                )
            )
            wave_inf.update(
                _render_wave(
                    infs,
                    width=self.chart_width,
                    height=self.chart_height,
                    y_max=y_max,
                    title="Inference latency",
                )
            )
            wave_emb.update(
                _render_wave(
                    embeds,
                    width=self.chart_width,
                    height=self.chart_height,
                    y_max=y_max,
                    title="Embedding latency",
                )
            )
            wave_ret.update(
                _render_wave(
                    rets,
                    width=self.chart_width,
                    height=self.chart_height,
                    y_max=y_max,
                    title="Retrieval latency",
                )
            )

            phase_mix.update(_render_phase_bar(latest, width=min(70, self.chart_width + 20)))

    return LatencyOscilloscopeApp


def run_latency_oscilloscope(
    *,
    source: str,
    jsonl_path: Path,
    max_points: int,
    poll_interval: float,
    chart_height: int,
    chart_width: int,
    max_latency: float,
    ma_window: int,
    spike_z: float,
    spike_min_samples: int,
    alert_total: float,
    alert_embed: float,
    alert_retrieval: float,
    alert_inference: float,
) -> int:
    app_cls = create_latency_oscilloscope_app(
        source=source,
        jsonl_path=jsonl_path,
        max_points=max_points,
        poll_interval=poll_interval,
        chart_height=chart_height,
        chart_width=chart_width,
        max_latency=max_latency,
        ma_window=ma_window,
        spike_z=spike_z,
        spike_min_samples=spike_min_samples,
        alert_total=alert_total,
        alert_embed=alert_embed,
        alert_retrieval=alert_retrieval,
        alert_inference=alert_inference,
    )
    app = app_cls()
    app.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="LIARA latency oscilloscope (Textual TUI)")
    p.add_argument("--source", choices=["demo", "jsonl"], default="demo", help="Data source")
    p.add_argument(
        "--jsonl",
        default="logs/services/orchestrator/latency_scope.jsonl",
        help="JSONL path for --source jsonl",
    )
    p.add_argument("--max-points", type=int, default=120, help="Samples kept in memory")
    p.add_argument("--poll", type=float, default=0.5, help="Poll/update interval seconds")
    p.add_argument("--chart-height", type=int, default=10, help="ASCII chart height")
    p.add_argument("--chart-width", type=int, default=90, help="ASCII chart width")
    p.add_argument("--max-latency", type=float, default=20.0, help="Y-axis floor in seconds")
    p.add_argument("--ma-window", type=int, default=8, help="Moving average window")
    p.add_argument("--spike-z", type=float, default=2.5, help="Spike z-score threshold")
    p.add_argument("--spike-min-samples", type=int, default=12, help="Min samples before spike detection")
    p.add_argument("--alert-total", type=float, default=20.0, help="Threshold alert for total latency (s)")
    p.add_argument("--alert-embed", type=float, default=2.5, help="Threshold alert for embedding latency (s)")
    p.add_argument("--alert-retrieval", type=float, default=1.2, help="Threshold alert for retrieval latency (s)")
    p.add_argument("--alert-inference", type=float, default=18.0, help="Threshold alert for inference latency (s)")
    args = p.parse_args(argv)

    return run_latency_oscilloscope(
        source=args.source,
        jsonl_path=Path(args.jsonl),
        max_points=args.max_points,
        poll_interval=args.poll,
        chart_height=args.chart_height,
        chart_width=args.chart_width,
        max_latency=args.max_latency,
        ma_window=args.ma_window,
        spike_z=args.spike_z,
        spike_min_samples=args.spike_min_samples,
        alert_total=args.alert_total,
        alert_embed=args.alert_embed,
        alert_retrieval=args.alert_retrieval,
        alert_inference=args.alert_inference,
    )


if __name__ == "__main__":
    raise SystemExit(main())
