"""FastAPI dependency injection helpers for liara-api service."""

from __future__ import annotations

from typing import Any
from fastapi import Depends, Request

from services.api.security import Principal, get_verified_principal, require_admin_principal
from services.inference.tts_adapter import TtsServiceAdapter
from services.memory_adapter import MemoryServiceAdapter
from services.orchestrator import Orchestrator


def get_app_symbol(name: str, fallback: Any) -> Any:
    """Helper to look up symbol on services.api.app if monkeypatched in tests, else fallback."""
    import sys
    app_mod = sys.modules.get("services.api.app")
    if not app_mod:
        return fallback
    sym = getattr(app_mod, name, fallback)
    return sym if sym is not None else fallback


def get_memory_adapter(request: Request) -> MemoryServiceAdapter:
    """Dependency for MemoryServiceAdapter stored in app state."""
    return request.app.state.memory_adapter


def get_orchestrator(request: Request) -> Orchestrator:
    """Dependency for Orchestrator stored in app state."""
    return request.app.state.orchestrator


def get_tts_adapter(request: Request) -> TtsServiceAdapter:
    """Dependency for TtsServiceAdapter stored in app state."""
    return request.app.state.tts_adapter


def get_governance_service(request: Request) -> Any:
    """Dependency for GovernanceService stored in app state."""
    return request.app.state.governance_service
