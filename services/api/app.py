"""FastAPI app for the liara-api service."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import typing
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from services.config import Settings
from services.inference.gateway import InferenceGateway
from services.inference.tts_adapter import TtsServiceAdapter
from services.memory.store import BackedMemoryServiceStore, EphemeralMemoryStore, NullMemoryStore
from services.memory.tier_store import MemoryLayer
from services.memory_adapter import InProcessMemoryAdapter, MemoryServiceAdapter
from services.orchestrator import Orchestrator
from services.tools.builtin.sys_audit import log_judge_pre_action
from services.tools.coordinator import ToolCoordinator
from services.tools.governance import (
    load_sys_governance_proposals,
    sys_governance_store_path,
    sys_governance_events_path,
)
from services.workspace import get_workspace_status, list_workspace_artifacts

# Import Subrouters
from services.api.routers.artifacts import router as artifacts_router, _get_session_snapshot_best_effort, _resolve_effective_sandbox_root
from services.api.routers.chat import router as chat_router
from services.api.routers.compute import router as compute_router
from services.api.routers.governance import (
    router as governance_router,
    _sys_governance_store_path,
    _sys_governance_events_path,
    _sync_sys_governance_store,
    _persist_sys_governance_proposals,
    _append_sys_governance_event,
    _load_sys_governance_events,
)
from services.api.routers.operations import router as operations_router
from services.api.routers.speech import router as speech_router
from services.api.routers.system import router as system_router
from services.api.routers.tools import router as tools_router
from services.api.routers.ai_brain import router as ai_brain_router


def _cors_allowed_origins() -> list[str]:
    configured = os.getenv("LIARA_API_CORS_ALLOW_ORIGINS", "").strip()
    if configured:
        return [origin.strip() for origin in configured.split(",") if origin.strip()]

    return [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:3002",
        "http://127.0.0.1:3002",
    ]


def create_default_memory_adapter() -> MemoryServiceAdapter:
    """Create default in-process memory adapter."""
    session_store = EphemeralMemoryStore()
    fact_store = EphemeralMemoryStore()
    retrieval_store = EphemeralMemoryStore()

    memory_layer = MemoryLayer(
        session_store=session_store,
        fact_store=fact_store,
        retrieval_index=retrieval_store,
        graph_store=NullMemoryStore(),
    )
    return InProcessMemoryAdapter(memory_layer)


def create_default_orchestrator(memory_adapter: MemoryServiceAdapter) -> Orchestrator:
    """Create orchestrator with production defaults."""
    return Orchestrator(
        tool_coordinator=ToolCoordinator(),
        inference_gateway=InferenceGateway(),
        memory_layer=memory_adapter,
    )


async def _maybe_close(value: typing.Any) -> None:
    close_fn = getattr(value, "close", None)
    if not callable(close_fn):
        return
    result = close_fn()
    if inspect.isawaitable(result):
        await result


def create_api_app(
    orchestrator: typing.Any | None = None,
    memory_adapter: MemoryServiceAdapter | None = None,
    tts_adapter: TtsServiceAdapter | None = None,
) -> FastAPI:
    """Build FastAPI app by mounting modularized subrouters."""
    adapter = memory_adapter or create_default_memory_adapter()
    orch = orchestrator or create_default_orchestrator(adapter)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        logger = logging.getLogger("liara.api.startup")
        manager = None
        if Settings.LLAMA_CPP_MANAGED_BY_API:
            from services.inference.llama_cpp_server import get_llama_cpp_server_manager

            manager = get_llama_cpp_server_manager()
            try:
                logger.info("[API STARTUP] Starting llama-server for ll_ol_fallback provider...")
                started = await manager.start(verbose=True)
                if started:
                    logger.info("[API STARTUP] ✓ llama-server ready")
                else:
                    logger.warning("[API STARTUP] ⚠ llama-server startup timeout")
            except Exception as e:
                logger.error(f"[API STARTUP] ✗ llama-server startup error: {e}", exc_info=True)
        else:
            logger.info("[API STARTUP] llama-server lifecycle management disabled")

        yield

        if manager is not None:
            try:
                logger.info("[API SHUTDOWN] Stopping llama-server...")
                await manager.stop(verbose=True)
                logger.info("[API SHUTDOWN] ✓ llama-server stopped")
            except Exception as e:
                logger.error(f"[API SHUTDOWN] llama-server stop error: {e}", exc_info=True)

        await _maybe_close(adapter)

    app = FastAPI(title="liara-api", lifespan=_lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.memory_adapter = adapter
    app.state.orchestrator = orch
    app.state.tts_adapter = tts_adapter or TtsServiceAdapter(
        timeout_seconds=float(os.getenv("LIARA_TTS_TIMEOUT_SECONDS", "360"))
    )

    # Governance state setup
    proposals_path = sys_governance_store_path()
    events_path = sys_governance_events_path()
    app.state.sys_tool_proposals_path = proposals_path
    app.state.sys_tool_events_path = events_path
    app.state.sys_tool_proposals = load_sys_governance_proposals(proposals_path)
    app.state.sys_tool_governance_lock = asyncio.Lock()

    # Mount subrouters
    app.include_router(system_router)
    app.include_router(chat_router)
    app.include_router(tools_router)
    app.include_router(governance_router)
    app.include_router(speech_router)
    app.include_router(compute_router)
    app.include_router(operations_router)
    app.include_router(artifacts_router)
    app.include_router(ai_brain_router)

    return app


# Instantiate module-level default app instance for ASGI servers (uvicorn services.api.app:app)
app = create_api_app()
