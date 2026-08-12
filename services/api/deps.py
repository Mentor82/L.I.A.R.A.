"""FastAPI dependency injection helpers for liara-api service."""

from __future__ import annotations

from typing import Any
from fastapi import Request

from services.inference.tts_adapter import TtsServiceAdapter
from services.memory_adapter import MemoryServiceAdapter
from services.orchestrator import Orchestrator


def get_memory_adapter(request: Request) -> MemoryServiceAdapter:
    """Dependency for MemoryServiceAdapter stored in app state."""
    return request.app.state.memory_adapter


def get_orchestrator(request: Request) -> Orchestrator:
    """Dependency for Orchestrator stored in app state."""
    return request.app.state.orchestrator


def get_tts_adapter(request: Request) -> TtsServiceAdapter:
    """Dependency for TtsServiceAdapter stored in app state."""
    return request.app.state.tts_adapter
