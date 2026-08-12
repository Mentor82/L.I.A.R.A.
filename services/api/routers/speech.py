"""FastAPI router for MiniCPM Speech & TTS streaming endpoints."""

from __future__ import annotations

import os
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from services.api.deps import get_tts_adapter
from services.api.models import SpeechGenerationRequest, SpeechStreamRequest
from services.contracts import ChatArtifact, TtsGenerationRequest, TtsHealthResponse
from services.inference.audio_streaming import (
    AudioStreamEncodingError,
    codec_media_type,
    codec_sample_rate,
    encode_audio_stream,
    resolve_ffmpeg_path,
)
from services.inference.tts_adapter import (
    TtsAdapterError,
    TtsServiceAdapter,
    prepare_pcm_stream_artifact,
)


router = APIRouter(prefix="", tags=["speech"])


def _tts_http_exception(exc: TtsAdapterError) -> HTTPException:
    status_code = exc.status_code if exc.status_code and 400 <= exc.status_code < 600 else 502
    detail: dict[str, Any] = {
        "code": exc.code,
        "message": str(exc),
        "retryable": exc.retryable,
    }
    details = getattr(exc, "details", None)
    if details is not None:
        detail["details"] = details
    return HTTPException(status_code=status_code, detail=detail)


from services.shared.sandboxing import canonicalize_sandbox_root, get_global_sandbox_root
from services.shared.types import MemoryTier


async def _get_session_snapshot_best_effort(adapter: Any, session_id: str) -> dict[str, Any]:
    if not adapter or not hasattr(adapter, "get"):
        return {}
    try:
        snapshot = await adapter.get(MemoryTier.SESSION, f"session:{session_id}", default={})
        return snapshot if isinstance(snapshot, dict) else {}
    except Exception:
        return {}


def _resolve_effective_sandbox_root(explicit: str | None, snapshot: dict[str, Any]) -> str:
    candidate = explicit
    if not candidate:
        metadata = snapshot.get("metadata") or {}
        candidate = metadata.get("sandbox_root") if isinstance(metadata, dict) else None
    candidate = candidate or get_global_sandbox_root()
    return canonicalize_sandbox_root(candidate)


@router.get("/speech/health", response_model=TtsHealthResponse)
async def speech_health(
    response: Response,
    tts_adapter: TtsServiceAdapter = Depends(get_tts_adapter),
) -> TtsHealthResponse:
    response.headers["Cache-Control"] = "public, max-age=60, stale-while-revalidate=120"
    try:
        return await tts_adapter.health()
    except TtsAdapterError as exc:
        raise _tts_http_exception(exc) from exc


@router.post("/speech/generate", response_model=ChatArtifact)
async def generate_speech(
    request_body: SpeechGenerationRequest,
    request: Request,
    response: Response,
    tts_adapter: TtsServiceAdapter = Depends(get_tts_adapter),
) -> ChatArtifact:
    response.headers["Cache-Control"] = "no-store"
    adapter = request.app.state.memory_adapter
    snapshot = await _get_session_snapshot_best_effort(adapter, request_body.session_id)

    try:
        sandbox_root = _resolve_effective_sandbox_root(request_body.sandbox_root, snapshot)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    generation_request = TtsGenerationRequest(
        text=request_body.text,
        speaker_profile=request_body.speaker_profile,
        max_audio_tokens=request_body.max_audio_tokens,
        seed=request_body.seed,
    )
    try:
        return await tts_adapter.generate_artifact(
            generation_request,
            session_id=request_body.session_id,
            sandbox_root=sandbox_root,
            title="LIARA response",
        )
    except TtsAdapterError as exc:
        raise _tts_http_exception(exc) from exc


@router.post("/speech/stream")
async def stream_speech(
    request_body: SpeechStreamRequest,
    request: Request,
    tts_adapter: TtsServiceAdapter = Depends(get_tts_adapter),
) -> StreamingResponse:
    adapter = request.app.state.memory_adapter
    generation_request = TtsGenerationRequest(
        text=request_body.text,
        speaker_profile=request_body.speaker_profile,
        max_audio_tokens=request_body.max_audio_tokens,
        seed=request_body.seed,
    )
    encoder_path = None
    if request_body.codec != "pcm_s16le":
        try:
            encoder_path = resolve_ffmpeg_path()
        except AudioStreamEncodingError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "speech_codec_unavailable",
                    "message": str(exc),
                    "retryable": False,
                },
            ) from exc
    artifact_sink = None
    if request_body.persist_artifact:
        snapshot = await _get_session_snapshot_best_effort(adapter, request_body.session_id)
        sandbox_root = _resolve_effective_sandbox_root(request_body.sandbox_root, snapshot)
        artifact_sink = prepare_pcm_stream_artifact(
            session_id=request_body.session_id,
            sandbox_root=sandbox_root,
        )
    try:
        upstream = await tts_adapter.open_stream(generation_request)
    except TtsAdapterError as exc:
        raise _tts_http_exception(exc) from exc

    if request_body.codec == "pcm_s16le" and artifact_sink is None:
        async def direct_pcm_body():
            try:
                async for chunk in upstream.iter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        headers = {
            "X-Liara-TTS-Request-Id": upstream.request_id,
            "X-Liara-TTS-Stream-Contract": "audio_stream/v1",
            "X-Liara-TTS-Codec": request_body.codec,
            "X-Liara-TTS-Source-Sample-Rate": str(upstream.sample_rate),
            "X-Liara-TTS-Sample-Rate": str(upstream.sample_rate),
            "X-Liara-TTS-Channels": str(upstream.channels),
            "X-Liara-TTS-Mode": upstream.mode,
            "Cache-Control": "private, no-store",
        }
        return StreamingResponse(
            direct_pcm_body(),
            media_type=codec_media_type(request_body.codec),
            headers=headers,
        )

    pcm_complete = False

    async def pcm_source():
        nonlocal pcm_complete
        try:
            async for chunk in upstream.iter_bytes():
                if artifact_sink is not None:
                    artifact_sink.write(chunk)
                yield chunk
            pcm_complete = True
        except BaseException:
            raise

    encoded_stream = encode_audio_stream(
        pcm_source(),
        codec=request_body.codec,
        ffmpeg_path=encoder_path,
    )
    response_complete = False

    async def encoded_body():
        nonlocal response_complete
        try:
            async for chunk in encoded_stream:
                yield chunk
            response_complete = True
        finally:
            await encoded_stream.aclose()
            await upstream.aclose()
            if artifact_sink is not None:
                if pcm_complete and response_complete:
                    try:
                        artifact_sink.commit()
                    except Exception as commit_exc:
                        artifact_sink.discard()
                        raise commit_exc
                else:
                    artifact_sink.discard()

    headers = {
        "X-Liara-TTS-Request-Id": upstream.request_id,
        "X-Liara-TTS-Stream-Contract": "audio_stream/v1",
        "X-Liara-TTS-Codec": request_body.codec,
        "X-Liara-TTS-Source-Sample-Rate": str(upstream.sample_rate),
        "X-Liara-TTS-Sample-Rate": str(codec_sample_rate(request_body.codec)),
        "X-Liara-TTS-Channels": str(upstream.channels),
        "X-Liara-TTS-Mode": upstream.mode,
        "Cache-Control": "private, no-store",
    }
    if artifact_sink is not None:
        headers.update(
            {
                "X-Liara-TTS-Artifact-URL": artifact_sink.url,
                "X-Liara-TTS-Artifact-Format": "wav",
                "X-Liara-TTS-Artifact-Commit": "on-complete",
            }
        )

    return StreamingResponse(
        encoded_body(),
        media_type=codec_media_type(request_body.codec),
        headers=headers,
    )
