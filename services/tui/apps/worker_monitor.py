#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""App entrypoint wrapper for worker monitor."""

from __future__ import annotations

from services.tui.worker_monitor import main, run_worker_monitor


if __name__ == "__main__":
    raise SystemExit(main())
