#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""App entrypoint wrapper for admin console."""

from __future__ import annotations

from services.tui.admin_console import main, run_admin_console


if __name__ == "__main__":
    raise SystemExit(main())
