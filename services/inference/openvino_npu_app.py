"""Standalone OpenVINO NPU inference app.

Runs as an independent FastAPI instance so OpenVINO inference can be
invoked directly outside the main orchestrator path.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from services.contracts import (
    InferenceRequest,
    InferenceResult,
    TtsErrorResponse,
    TtsGenerationRequest,
    TtsHealthResponse,
    VisionRequest,
    VisionResponse,
)
from services.inference.minicpmo_tts.config import TtsServiceConfig
from services.inference.minicpmo_tts.engine import OpenVINOTtsEngine, TtsServiceError
from services.inference.providers import OpenVINOProvider


_LOGGER = logging.getLogger("uvicorn.error")


def _load_local_dotenv() -> None:
    """Load project .env when the app is launched standalone."""
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return

    candidates = [
        Path(__file__).resolve().parents[2] / ".env",
        Path.cwd() / ".env",
    ]
    for env_path in candidates:
        if env_path.exists():
            load_dotenv(env_path, override=True)
            return


def _ensure_openvino_genai_python_path() -> str | None:
    """Ensure local OpenVINO GenAI Python package path is importable.

    Priority:
    1) OPENVINO_GENAI_PYTHON_DIR (if set)
    2) C:\\openvino_genai\\python (if present)
    """

    configured = (os.getenv("OPENVINO_GENAI_PYTHON_DIR", "") or "").strip()
    candidates = [configured] if configured else []
    candidates.append("C:/openvino_genai/python")

    for raw in candidates:
        if not raw:
            continue
        candidate = Path(raw)
        package_init = candidate / "openvino_genai" / "__init__.py"
        if package_init.exists():
            resolved = str(candidate.resolve())
            if resolved not in sys.path:
                sys.path.insert(0, resolved)
            return resolved
    return None


def _resolve_model_dir() -> str | None:
    def _normalize_candidate(raw: str) -> Path:
        text = raw.strip()
        if os.name == "nt" and text.startswith("/ai/"):
            text = f"C:{text}"
        return Path(text)

    def _looks_like_model_dir(path: Path) -> bool:
        if not path.is_dir():
            return False
        if (path / "openvino_model.xml").exists():
            return True
        return any(path.glob("*.xml")) and any(path.glob("*.json"))

    def _resolve_from_candidate(raw: str) -> str | None:
        candidate = _normalize_candidate(raw)
        if not candidate.exists():
            return None

        if _looks_like_model_dir(candidate):
            return str(candidate.resolve())

        preferred_subdir = (os.getenv("OPENVINO_NPU_MODEL_SUBDIR", "") or "").strip()
        preferred_names = [
            preferred_subdir,
            "Qwen2.5-1B-Instruct-fp16-test-ov",
            "Qwen2.5-1B-Instruct",
            "Qwen2.5-Coder-0.5B-fp16-test-ov",
            "MiniCPM-o-2.6-fp16-test-ov",
        ]
        for name in preferred_names:
            if not name:
                continue
            sub = candidate / name
            if _looks_like_model_dir(sub):
                return str(sub.resolve())

        for sub in candidate.iterdir():
            if sub.is_dir() and _looks_like_model_dir(sub):
                return str(sub.resolve())

        return str(candidate.resolve())

    direct = (os.getenv("OPENVINO_NPU_MODEL_DIR", "") or "").strip()
    if direct:
        resolved = _resolve_from_candidate(direct)
        if resolved:
            return resolved

    genai = (os.getenv("OPENVINO_GENAI_MODEL_DIR", "") or "").strip()
    if genai:
        resolved = _resolve_from_candidate(genai)
        if resolved:
            return resolved

    generic = (os.getenv("OPENVINO_MODEL_DIR", "") or "").strip()
    if generic:
        resolved = _resolve_from_candidate(generic)
        if resolved:
            return resolved

    return None


def _resolve_device() -> str:
    return (
        (os.getenv("OPENVINO_NPU_DEVICE", "") or "").strip()
        or (os.getenv("OPENVINO_GENAI_DEVICE", "") or "").strip()
        or (os.getenv("OPENVINO_DEVICE", "") or "").strip()
        or "NPU"
    )


def _normalize_helper_json_output(raw_content: str) -> tuple[str, bool, str | None]:
    """Normalize LLM helper output to plain JSON when possible.

    Returns (content, normalized, parse_error).
    """

    text = (raw_content or "").strip()
    if not text:
        return raw_content, False, None

    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, flags=re.IGNORECASE | re.DOTALL)
    candidate = fenced.group(1).strip() if fenced else text

    # Some instruction-tuned VLMs apply Markdown escaping inside otherwise
    # valid JSON keys. ``\_`` is not a JSON escape sequence; undo only this
    # harmless formatting artifact before parsing.
    candidate = candidate.replace(r"\_", "_")

    if not ((candidate.startswith("{") and candidate.endswith("}")) or (candidate.startswith("[") and candidate.endswith("]"))):
        object_start = candidate.find("{")
        object_end = candidate.rfind("}")
        if object_start >= 0 and object_end > object_start:
            candidate = candidate[object_start : object_end + 1].strip()

    try:
        parsed = json.loads(candidate)
        normalized = json.dumps(parsed, ensure_ascii=True, separators=(",", ":"))
        return normalized, True, None
    except Exception as exc:
        return raw_content, False, str(exc)


class OpenVINOInferRequest(BaseModel):
    prompt: str = Field(min_length=1)
    max_tokens: int = Field(default=512, ge=1, le=8192)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    model: str | None = None


class OpenVINOHelperInferRequest(BaseModel):
    task_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    source_text: str = Field(min_length=1)
    expected_fields: list[str] = Field(default_factory=lambda: ["task_id", "key_points", "confidence"])
    max_tokens: int = Field(default=220, ge=1, le=8192)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    model: str | None = None


class OpenVINOHelperInferResponse(BaseModel):
    status: str
    provider: str
    model: str
    parsed: dict[str, Any] | list[Any] | None = None
    raw_content: str
    normalized: bool
    schema_ok: bool
    missing_fields: list[str] = Field(default_factory=list)
    parse_error: str | None = None
    inference_metadata: dict[str, Any] = Field(default_factory=dict)


_load_local_dotenv()
_OPENVINO_GENAI_PYTHON_PATH = _ensure_openvino_genai_python_path()
_MODEL_DIR = _resolve_model_dir()
_DEVICE = _resolve_device()
_PROVIDER = OpenVINOProvider(
    model_dir=_MODEL_DIR,
    device=_DEVICE,
    model_fallback=(os.getenv("OPENVINO_NPU_MODEL_NAME", "openvino-npu") or "openvino-npu").strip(),
)
_TTS_ENGINE = OpenVINOTtsEngine(TtsServiceConfig.from_env())

app = FastAPI(title="liara-openvino-npu", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    configured = bool(_MODEL_DIR)
    vision_configured = bool(
        _MODEL_DIR
        and (Path(_MODEL_DIR) / "openvino_language_model.xml").exists()
        and (Path(_MODEL_DIR) / "openvino_vision_embeddings_model.xml").exists()
    )
    tts_health = _TTS_ENGINE.health()
    return {
        "status": "ok" if configured else "degraded",
        "provider": "openvino",
        "mode": "standalone_npu_instance",
        "device": _DEVICE,
        "model_dir_configured": configured,
        "model_dir": _MODEL_DIR,
        "openvino_genai_python_path": _OPENVINO_GENAI_PYTHON_PATH,
        "capabilities": {
            "vision": {
                "status": "ready" if vision_configured else "unavailable",
                "mode": "openvino_genai_vlm",
                "loaded": _PROVIDER._pipeline is not None and _PROVIDER._pipeline_kind == "vlm",
                "max_images": 4,
            },
            "tts": {
                "status": tts_health.status,
                "mode": tts_health.mode,
                "loaded": tts_health.loaded,
            }
        },
    }


@app.exception_handler(RequestValidationError)
async def request_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path not in {"/tts/generate", "/tts/stream"}:
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    request_id = request.headers.get("X-Liara-TTS-Request-Id") or str(uuid.uuid4())
    payload = TtsErrorResponse(
        request_id=request_id,
        code="invalid_tts_request",
        message="TTS request validation failed",
    )
    return JSONResponse(status_code=400, content=payload.model_dump())


@app.get("/tts/health", response_model=TtsHealthResponse)
async def tts_health() -> TtsHealthResponse:
    return _TTS_ENGINE.health()


@app.post(
    "/tts/generate",
    responses={
        200: {"content": {"audio/wav": {}}},
        400: {"model": TtsErrorResponse},
        409: {"model": TtsErrorResponse},
        429: {"model": TtsErrorResponse},
        503: {"model": TtsErrorResponse},
        504: {"model": TtsErrorResponse},
    },
)
async def tts_generate(request: TtsGenerationRequest) -> Response:
    request_id = str(uuid.uuid4())
    try:
        result = await _TTS_ENGINE.generate(request)
    except TtsServiceError as exc:
        _LOGGER.warning(
            json.dumps(
                {
                    "event": "tts_request_failed",
                    "request_id": request_id,
                    "code": exc.code,
                    "status_code": exc.status_code,
                    "retryable": exc.retryable,
                },
                separators=(",", ":"),
            )
        )
        payload = TtsErrorResponse(
            request_id=request_id,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
        )
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump())

    timings = result.timings_ms
    _LOGGER.info(
        json.dumps(
            {
                "event": "tts_request_completed",
                "request_id": request_id,
                "mode": result.mode,
                "audio_tokens": result.audio_tokens,
                "sample_rate": result.sample_rate,
                "duration_ms": result.duration_ms,
                "timings_ms": {name: round(value, 2) for name, value in timings.items()},
            },
            separators=(",", ":"),
        )
    )
    server_timing = ", ".join(
        f"{name};dur={timings[name]:.2f}"
        for name in ("load", "generate", "dvae", "vocos", "total")
        if name in timings
    )
    return Response(
        content=result.wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Liara-TTS-Request-Id": request_id,
            "X-Liara-TTS-Audio-Tokens": str(result.audio_tokens),
            "X-Liara-TTS-Sample-Rate": str(result.sample_rate),
            "X-Liara-TTS-Duration-Ms": str(result.duration_ms),
            "X-Liara-TTS-Mode": result.mode,
            "Server-Timing": server_timing,
        },
    )


@app.post(
    "/tts/stream",
    response_model=None,
    responses={
        200: {"content": {"audio/x-pcm": {}}},
        400: {"model": TtsErrorResponse},
        409: {"model": TtsErrorResponse},
        429: {"model": TtsErrorResponse},
        503: {"model": TtsErrorResponse},
        504: {"model": TtsErrorResponse},
    },
)
async def tts_stream(request: TtsGenerationRequest, http_request: Request) -> Response:
    request_id = str(uuid.uuid4())
    try:
        stream = await _TTS_ENGINE.stream(request)
    except TtsServiceError as exc:
        payload = TtsErrorResponse(
            request_id=request_id,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
        )
        return JSONResponse(status_code=exc.status_code, content=payload.model_dump())

    async def pcm_body():
        chunk_count = 0
        byte_count = 0
        try:
            async for chunk in stream:
                if await http_request.is_disconnected():
                    break
                chunk_count += 1
                byte_count += len(chunk.pcm_bytes)
                yield chunk.pcm_bytes
        finally:
            await stream.aclose()
            _LOGGER.info(
                json.dumps(
                    {
                        "event": "tts_stream_closed",
                        "request_id": request_id,
                        "chunks": chunk_count,
                        "bytes": byte_count,
                    },
                    separators=(",", ":"),
                )
            )

    return StreamingResponse(
        pcm_body(),
        media_type="audio/x-pcm;format=s16le;rate=24000;channels=1",
        headers={
            "X-Liara-TTS-Request-Id": request_id,
            "X-Liara-TTS-Stream-Contract": "audio_stream/v1",
            "X-Liara-TTS-Codec": "pcm_s16le",
            "X-Liara-TTS-Sample-Rate": "24000",
            "X-Liara-TTS-Channels": "1",
            "X-Liara-TTS-Mode": _TTS_ENGINE.config.mode,
        },
    )


@app.post("/infer", response_model=InferenceResult)
async def infer(request: OpenVINOInferRequest) -> InferenceResult:
    inference_request = InferenceRequest(
        prompt=request.prompt,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        provider="openvino",
        model=request.model,
    )
    result = await _PROVIDER.infer(inference_request)
    metadata = dict(result.metadata or {})
    metadata.setdefault("instance", "openvino_npu_standalone")
    metadata.setdefault("device", _DEVICE)

    if result.status == "success" and result.content:
        normalized_content, normalized, parse_error = _normalize_helper_json_output(result.content)
        if normalized:
            result.content = normalized_content
        metadata["helper_output_normalized"] = normalized
        if parse_error:
            metadata["helper_output_parse_error"] = parse_error

    result.metadata = metadata
    return result


@app.post("/vision/analyze", response_model=VisionResponse)
async def analyze_vision(request: VisionRequest) -> VisionResponse:
    """Canonical visual perception endpoint; it never accepts remote URLs."""
    return await _PROVIDER.infer_vision(request)


@app.post("/infer/helper", response_model=OpenVINOHelperInferResponse)
async def infer_helper(request: OpenVINOHelperInferRequest) -> OpenVINOHelperInferResponse:
    fields = ",".join(request.expected_fields)
    helper_prompt = (
        "Du bist NPU-Helper fuer einen Main-Orchestrator. "
        f"Aufgabe: task_id={request.task_id}; task_type={request.task_type}; "
        f"source_text=\"{request.source_text}\"; "
        f"expected_output=JSON mit Feldern {fields}. "
        "Regeln: Nur valides JSON, keine Erklaerung ausserhalb JSON."
    )

    inference_request = InferenceRequest(
        prompt=helper_prompt,
        max_tokens=request.max_tokens,
        temperature=request.temperature,
        provider="openvino",
        model=request.model,
    )

    result = await _PROVIDER.infer(inference_request)
    metadata = dict(result.metadata or {})
    metadata.setdefault("instance", "openvino_npu_standalone")
    metadata.setdefault("device", _DEVICE)

    normalized = False
    parse_error: str | None = None
    parsed: dict[str, Any] | list[Any] | None = None

    content = result.content or ""
    if result.status == "success" and content:
        normalized_content, normalized, parse_error = _normalize_helper_json_output(content)
        content = normalized_content if normalized else content
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = None

    missing_fields: list[str] = []
    schema_ok = False
    if isinstance(parsed, dict):
        missing_fields = [field for field in request.expected_fields if field not in parsed]
        schema_ok = len(missing_fields) == 0

    metadata["helper_output_normalized"] = normalized
    if parse_error:
        metadata["helper_output_parse_error"] = parse_error

    return OpenVINOHelperInferResponse(
        status=result.status,
        provider=result.provider,
        model=result.model,
        parsed=parsed,
        raw_content=content,
        normalized=normalized,
        schema_ok=schema_ok,
        missing_fields=missing_fields,
        parse_error=parse_error,
        inference_metadata=metadata,
    )
