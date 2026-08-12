#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility entrypoint for the modular LIARA shell implementation."""

from __future__ import annotations

from services.tui.liara_shell_modules import main, run_shell


if __name__ == "__main__":
    raise SystemExit(main())
