"""embedding-dev FastAPI service (port 8033) — OpenVINO model in memory."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .engine import EMBEDDING_DIMS, DevEmbeddingEngine, DevEngineConfig


class EmbeddingGenerateRequest(BaseModel):
    input_text: str = Field(min_length=1)
    normalize: bool = True


class EmbeddingGenerateResponse(BaseModel):
    embedding: list[float]
    dimensions: int
    device: str
    model: str


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = DevEngineConfig(
        model_path=_env("EMBEDDING_DEV_MODEL_PATH", "c:/ai/models/Qwen3-Embedding-0.6B-fp16-ov"),
        device=_env("EMBEDDING_DEV_DEVICE", "NPU"),
    )
    engine = DevEmbeddingEngine(config)
    app.state.engine = engine

    timeout = float(_env("EMBEDDING_DEV_STARTUP_TIMEOUT_SECONDS", "120"))
    try:
        await asyncio.wait_for(asyncio.to_thread(engine.load), timeout=timeout)
    except Exception as exc:
        engine._error = str(exc)

    yield
    # Model stays in memory until process exits — nothing to clean up explicitly.


app = FastAPI(title="embedding-dev", lifespan=lifespan)


@app.get("/health")
async def health():
    engine: DevEmbeddingEngine = app.state.engine
    if engine.is_ready:
        return {
            "status": "ok",
            "device": engine.config.device,
            "execution_devices": engine.execution_devices,
            "model": engine.config.model_path,
            "dimensions": EMBEDDING_DIMS,
        }
    return {
        "status": "error",
        "error": engine._error,
        "device": engine.config.device,
        "execution_devices": engine.execution_devices,
        "dimensions": EMBEDDING_DIMS,
    }


@app.post("/embedding/generate", response_model=EmbeddingGenerateResponse)
async def generate_embedding(request: EmbeddingGenerateRequest):
    engine: DevEmbeddingEngine = app.state.engine
    if not engine.is_ready:
        raise HTTPException(status_code=503, detail=engine.error or "embedding engine not ready")

    try:
        vector = engine.embed(request.input_text, normalize=request.normalize)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"embedding generation failed: {exc}") from exc

    return EmbeddingGenerateResponse(
        embedding=vector,
        dimensions=len(vector),
        device=engine.config.device,
        model=engine.config.model_path,
    )