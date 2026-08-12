#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""App entrypoint wrapper for latency oscilloscope."""

from __future__ import annotations

from services.tui.latency_oscilloscope import main, run_latency_oscilloscope


if __name__ == "__main__":
    raise SystemExit(main())
