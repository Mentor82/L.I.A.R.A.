"""liara-api package exports.

Uses lazy resolution to avoid importing `services.api.app` as a side effect of
`import services.api`, which otherwise triggers runpy warnings when the app is
executed as a module.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .app import (
        SessionResponse,
        app,
        create_api_app,
        create_default_memory_adapter,
        create_default_orchestrator,
    )

__all__ = [
    "SessionResponse",
    "app",
    "create_api_app",
    "create_default_memory_adapter",
    "create_default_orchestrator",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        module = import_module(".app", __name__)
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
