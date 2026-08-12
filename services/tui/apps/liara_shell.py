#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""App entrypoint wrapper for LIARA shell."""

from __future__ import annotations

from services.tui.liara_shell import main, run_shell


if __name__ == "__main__":
    raise SystemExit(main())
