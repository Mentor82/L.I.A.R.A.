"""FastAPI router for system and backend health endpoints."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Request, Response

from services.config import Settings
from services.contracts import MemoryHealthResponse
from services.inference.llama_cpp_server import LlamaCppServerManager
from services.memory.store import BackedMemoryServiceStore


router = APIRouter(tags=["system"])


def _postgres_backend_usable() -> bool:
    return bool(getattr(Settings, "POSTGRES_URL", os.getenv("POSTGRES_URL")))


def _redis_backend_usable() -> bool:
    return bool(getattr(Settings, "REDIS_URL", os.getenv("REDIS_URL")))


def _qdrant_backend_usable() -> bool:
    return bool(getattr(Settings, "QDRANT_URL", os.getenv("QDRANT_URL")))


def _neo4j_backend_usable() -> bool:
    return bool(getattr(Settings, "NEO4J_URL", os.getenv("NEO4J_URL")))


import os
import json
from hashlib import sha256


def _cacheable_json_response(payload: dict[str, Any], request: Request, cache_control: str) -> Response:
    data_bytes = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    etag = f'"{sha256(data_bytes).hexdigest()[:16]}"'
    if request.headers.get("If-None-Match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": cache_control})
    return Response(
        content=data_bytes,
        status_code=200,
        media_type="application/json",
        headers={"ETag": etag, "Cache-Control": cache_control},
    )


@router.get("/health")
async def health(request: Request) -> Response:
    payload = {
        "status": "ok",
        "service": "liara-api",
        "memory_mode": (getattr(Settings, "MEMORY_MODE", "in_process") or "in_process"),
        "backends_configured": {
            "postgres": _postgres_backend_usable(),
            "redis": _redis_backend_usable(),
            "qdrant": _qdrant_backend_usable(),
            "chroma": bool(Settings.CHROMA_HOST),
            "neo4j": _neo4j_backend_usable(),
            "embedding": bool(Settings.EMBEDDING_SERVICE_BASE_URL),
        },
    }
    return _cacheable_json_response(payload, request, cache_control="public, max-age=5, stale-while-revalidate=10")


@router.get("/health/backends", response_model=MemoryHealthResponse)
async def health_backends(response: Response) -> MemoryHealthResponse:
    response.headers["Cache-Control"] = "no-store"
    store = BackedMemoryServiceStore()
    try:
        return await store.health_backends()
    finally:
        await store.close()


@router.get("/admin/llama-backends")
async def llama_backends(response: Response) -> dict[str, Any]:
    """List available llama.cpp build variants and show which one is active."""
    response.headers["Cache-Control"] = "no-store"

    build_base_dir = Settings.LLAMA_CPP_BUILD_BASE_DIR
    configured_variant = Settings.LLAMA_CPP_BUILD_VARIANT

    available: list[dict[str, Any]] = []
    for variant in LlamaCppServerManager.AVAILABLE_BUILDS:
        try:
            path = LlamaCppServerManager.get_build_path(variant)
            available.append({"variant": variant, "path": str(path), "present": True})
        except FileNotFoundError:
            available.append({"variant": variant, "path": None, "present": False})

    try:
        active_variant, active_path = LlamaCppServerManager.find_available_build(
            preferred_variant=configured_variant
        )
    except FileNotFoundError:
        active_variant = None
        active_path = None

    return {
        "build_base_dir": build_base_dir,
        "configured_variant": configured_variant,
        "active_variant": active_variant,
        "active_binary": str(active_path) if active_path else None,
        "available_builds": available,
    }
