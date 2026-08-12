"""Lazy Textual symbol loading with consistent install hints."""

from __future__ import annotations

import importlib
from typing import Any


_MODULE_HINTS: dict[str, str] = {
    "services.tui.worker_monitor": "services.tui.worker_monitor",
    "services.tui.memory_inspector": "services.tui.memory_inspector",
    "services.tui.admin_console": "services.tui.admin_console",
    "services.tui.launcher": "services.tui.launcher",
}


def load_textual_symbols(caller_module: str) -> tuple[Any, Any, Any, Any]:
    """Return textual app, binding, container, and widget modules."""
    hint = _MODULE_HINTS.get(caller_module, caller_module)
    try:
        app_mod = importlib.import_module("textual.app")
        binding_mod = importlib.import_module("textual.binding")
        containers_mod = importlib.import_module("textual.containers")
        widgets_mod = importlib.import_module("textual.widgets")
    except ImportError as exc:
        raise RuntimeError(
            f"textual is required for {hint}. Install it with: pip install textual"
        ) from exc

    return app_mod, binding_mod, containers_mod, widgets_mod
