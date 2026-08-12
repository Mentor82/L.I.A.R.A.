#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""App entrypoint wrapper for memory inspector."""

from __future__ import annotations

from services.tui.memory_inspector import main, run_memory_inspector


if __name__ == "__main__":
    raise SystemExit(main())
