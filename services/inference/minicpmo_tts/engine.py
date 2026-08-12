"""Async lifecycle and concurrency boundary for MiniCPM-o TTS."""

from __future__ import annotations

import asyncio
import math
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass, replace
from typing import Any, Protocol

import numpy as np

from services.contracts import SpeechPlanSegment, TtsDevicePlacement, TtsGenerationRequest, TtsHealthResponse
from services.speech import SpeechPlanner

from .artifacts import TtsArtifactPaths, validate_bundle
from .audio import apply_edge_fade, encode_pcm16, encode_pcm16_wav
from .config import TtsServiceConfig
from .runtime import MiniCPMOTtsRuntime, TtsCompiledModels, TtsRuntimeResult


class TtsBackend(Protocol):
    def generate(
        self,
        text: str,
        max_audio_tokens: int,
        seed: int,
        cancelled: Callable[[], bool],
    ) -> TtsRuntimeResult: ...


class TtsServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class TtsEngineResult:
    wav_bytes: bytes
    audio_tokens: int
    sample_rate: int
    duration_ms: int
    mode: str
    timings_ms: dict[str, float]


@dataclass(frozen=True)
class TtsPcmChunk:
    """One ordered binary chunk from the format-independent speech producer."""

    sequence: int
    kind: str
    pcm_bytes: bytes
    sample_rate: int
    duration_ms: int
    audio_tokens: int = 0
    semantic_role: str | None = None
    timings_ms: dict[str, float] | None = None


_SPEECH_SEGMENT_MAX_CHARS = 70
_SPEECH_SEGMENT_MIN_CHARS = 36


def _generate_segmented_pcm(
    backend: TtsBackend,
    text: str,
    max_audio_tokens: int,
    seed: int,
    cancelled: Callable[[], bool],
) -> Iterator[TtsPcmChunk]:
    planner = SpeechPlanner(max_chars=_SPEECH_SEGMENT_MAX_CHARS)
    pending = list(planner.plan(text).segments)
    generation_index = 0
    sequence = 0
    while pending:
        segment = pending.pop(0)
        result = backend.generate(
            segment.text,
            max_audio_tokens,
            seed + generation_index,
            cancelled,
        )
        generation_index += 1
        generated_tokens = int(result.audio_codes.shape[2])
        if generated_tokens >= max_audio_tokens:
            if len(segment.text) > _SPEECH_SEGMENT_MIN_CHARS:
                smaller = planner.split_segment(
                    segment,
                    max_chars=max(_SPEECH_SEGMENT_MIN_CHARS, len(segment.text) // 2),
                )
                if len(smaller) > 1:
                    pending[0:0] = smaller
                    continue
            raise TtsServiceError(
                "tts_segment_truncated",
                "TTS could not complete a speech segment within the audio token limit",
                status_code=503,
            )
        pcm_bytes = encode_pcm16(apply_edge_fade(result.waveform, result.sample_rate))
        yield TtsPcmChunk(
            sequence=sequence,
            kind="audio",
            pcm_bytes=pcm_bytes,
            sample_rate=result.sample_rate,
            duration_ms=round(len(pcm_bytes) * 1000 / (2 * result.sample_rate)),
            audio_tokens=generated_tokens,
            semantic_role=segment.semantic_role,
            timings_ms={key: float(value) for key, value in result.timings_ms.items()},
        )
        sequence += 1
        if pending and segment.pause_after_ms:
            pause_frames = round(result.sample_rate * segment.pause_after_ms / 1000)
            yield TtsPcmChunk(
                sequence=sequence,
                kind="pause",
                pcm_bytes=b"\0\0" * pause_frames,
                sample_rate=result.sample_rate,
                duration_ms=segment.pause_after_ms,
                semantic_role=segment.semantic_role,
            )
            sequence += 1


def _next_chunk(iterator: Iterator[TtsPcmChunk]) -> TtsPcmChunk | None:
    try:
        return next(iterator)
    except StopIteration:
        return None


class _OpenVINOBackend:
    def __init__(self, runtime: MiniCPMOTtsRuntime, tokenizer: Any, speaker_hidden_state: np.ndarray):
        self.runtime = runtime
        self.tokenizer = tokenizer
        self.speaker_hidden_state = speaker_hidden_state

    def generate(
        self,
        text: str,
        max_audio_tokens: int,
        seed: int,
        cancelled: Callable[[], bool],
    ) -> TtsRuntimeResult:
        config = self.runtime.config
        text_tokens = self.tokenizer.encode(text, add_special_tokens=False)[: config.reserved_text_tokens]
        normalized_text = self.tokenizer.decode(text_tokens, add_special_tokens=False)
        padding_count = config.reserved_text_tokens - len(text_tokens)
        padding = "[Etts]" + "[PAD]" * (padding_count - 1) if padding_count else ""
        prepared = f"[Stts][spk_emb]{normalized_text}{padding}[Ptts]"
        input_ids = np.asarray(
            self.tokenizer.encode(prepared, add_special_tokens=False), dtype=np.int64
        )[None, :]
        text_mask = np.zeros((config.condition_length,), dtype=np.int8)
        text_mask[: 1 + 1 + len(text_tokens) + 1] = 1
        text_mask[-1] = 1
        return self.runtime.generate(
            input_ids=input_ids,
            text_mask=text_mask,
            speaker_hidden_state=self.speaker_hidden_state,
            max_audio_tokens=max_audio_tokens,
            seed=seed,
            cancelled=cancelled,
        )


class OpenVINOTtsEngine:
    def __init__(
        self,
        config: TtsServiceConfig,
        runtime_loader: Callable[[TtsServiceConfig], TtsBackend] | None = None,
    ):
        self.config = config
        self._runtime_loader = runtime_loader or _load_openvino_backend
        self._backend: TtsBackend | None = None
        self._status = "disabled" if not config.enabled else "unloaded"
        self._last_error: str | None = None
        self._load_lock = asyncio.Lock()
        self._generate_lock = asyncio.Lock()
        self._admission_lock = asyncio.Lock()
        self._admitted = 0
        self._waiting = 0
        self._request_count = 0
        self._failure_count = 0
        if config.enabled and runtime_loader is None:
            try:
                validate_bundle(TtsArtifactPaths.from_bundle(config.model_dir, config.speaker_profile))
            except Exception as exc:
                self._status = "failed"
                self._last_error = type(exc).__name__
                self._failure_count = 1

    def health(self) -> TtsHealthResponse:
        if self.config.mode == "mixed_npu_cpu":
            devices = TtsDevicePlacement(transformer="NPU", dvae="NPU", vocos="CPU")
        else:
            devices = TtsDevicePlacement(transformer="CPU", dvae="CPU", vocos="CPU")
        return TtsHealthResponse(
            status=self._status,
            mode=self.config.mode,
            devices=devices,
            model_dir=str(self.config.model_dir),
            speaker_profile=self.config.speaker_profile,
            loaded=self._backend is not None,
            queue_depth=self._waiting,
            request_count=self._request_count,
            failure_count=self._failure_count,
            last_error=self._last_error,
        )

    async def generate(self, request: TtsGenerationRequest) -> TtsEngineResult:
        started = time.perf_counter()
        pcm_parts: list[bytes] = []
        sample_rate: int | None = None
        audio_tokens = 0
        segment_count = 0
        planned_pause_ms = 0
        timings: dict[str, float] = {}
        stream = await self.stream(request)
        try:
            async for chunk in stream:
                if sample_rate is None:
                    sample_rate = chunk.sample_rate
                elif chunk.sample_rate != sample_rate:
                    raise TtsServiceError(
                        "tts_sample_rate_changed",
                        "TTS stream changed sample rate between segments",
                        status_code=503,
                    )
                pcm_parts.append(chunk.pcm_bytes)
                if chunk.kind == "audio":
                    audio_tokens += chunk.audio_tokens
                    segment_count += 1
                    for key, value in (chunk.timings_ms or {}).items():
                        timings[key] = timings.get(key, 0.0) + float(value)
                elif chunk.kind == "pause":
                    planned_pause_ms += chunk.duration_ms
        finally:
            await stream.aclose()

        if sample_rate is None or not pcm_parts:
            raise TtsServiceError("tts_generation_failed", "TTS generated no audio", status_code=503)
        pcm_bytes = b"".join(pcm_parts)
        timings["segment_count"] = float(segment_count)
        timings["planned_pause_ms"] = float(planned_pause_ms)
        timings["total"] = (time.perf_counter() - started) * 1000
        return TtsEngineResult(
            wav_bytes=encode_pcm16_wav(pcm_bytes, sample_rate),
            audio_tokens=audio_tokens,
            sample_rate=sample_rate,
            duration_ms=round(len(pcm_bytes) * 1000 / (2 * sample_rate)),
            mode=self.config.mode,
            timings_ms=timings,
        )

    async def stream(self, request: TtsGenerationRequest) -> AsyncIterator[TtsPcmChunk]:
        """Open an ordered, backpressure-aware PCM stream for one speech request."""
        self._validate_request(request)
        backend, load_ms = await self._get_backend()
        await self._admit()
        acquired = False
        cancel_event = threading.Event()
        try:
            try:
                await asyncio.wait_for(
                    self._generate_lock.acquire(), timeout=self.config.queue_timeout_seconds
                )
            except TimeoutError as exc:
                async with self._admission_lock:
                    self._waiting = max(0, self._waiting - 1)
                raise TtsServiceError(
                    "tts_queue_timeout", "TTS queue wait timed out", status_code=429, retryable=True
                ) from exc
            acquired = True
            async with self._admission_lock:
                self._waiting = max(0, self._waiting - 1)
            self._request_count += 1
            seed = request.seed if request.seed is not None else int.from_bytes(os_urandom(4), "little")
            iterator = _generate_segmented_pcm(
                backend,
                request.text,
                request.max_audio_tokens,
                seed,
                cancel_event.is_set,
            )
            deadline = asyncio.get_running_loop().time() + self.config.request_timeout_seconds

            async def iterate() -> AsyncIterator[TtsPcmChunk]:
                first = True
                sample_rate: int | None = None
                try:
                    while True:
                        remaining = deadline - asyncio.get_running_loop().time()
                        if remaining <= 0:
                            raise TimeoutError
                        chunk = await asyncio.wait_for(
                            asyncio.to_thread(_next_chunk, iterator),
                            timeout=remaining,
                        )
                        if chunk is None:
                            break
                        if sample_rate is None:
                            sample_rate = chunk.sample_rate
                        elif chunk.sample_rate != sample_rate:
                            raise TtsServiceError(
                                "tts_sample_rate_changed",
                                "TTS stream changed sample rate between segments",
                                status_code=503,
                            )
                        if first:
                            chunk_timings = dict(chunk.timings_ms or {})
                            chunk_timings["load"] = load_ms
                            chunk = replace(chunk, timings_ms=chunk_timings)
                            first = False
                        yield chunk
                except TimeoutError as exc:
                    self._failure_count += 1
                    raise TtsServiceError(
                        "tts_timeout",
                        "TTS generation budget was exceeded",
                        status_code=504,
                        retryable=True,
                    ) from exc
                except TtsServiceError:
                    raise
                except Exception as exc:
                    self._failure_count += 1
                    self._last_error = type(exc).__name__
                    self._status = "degraded" if self._backend is not None else "failed"
                    raise TtsServiceError(
                        "tts_generation_failed", "TTS generation failed", status_code=503
                    ) from exc
                finally:
                    cancel_event.set()
                    if acquired:
                        self._generate_lock.release()
                    async with self._admission_lock:
                        self._admitted = max(0, self._admitted - 1)

            return iterate()
        except BaseException:
            cancel_event.set()
            if acquired:
                self._generate_lock.release()
            async with self._admission_lock:
                self._admitted = max(0, self._admitted - 1)
            raise

    def _validate_request(self, request: TtsGenerationRequest) -> None:
        if not self.config.enabled:
            raise TtsServiceError("tts_disabled", "TTS is disabled", status_code=503)
        if len(request.text) > self.config.max_text_chars:
            raise TtsServiceError("text_too_long", "Text exceeds the configured limit", status_code=400)
        if request.max_audio_tokens > self.config.max_audio_tokens:
            raise TtsServiceError(
                "audio_token_limit",
                "Per-segment audio token budget exceeds the configured limit",
                status_code=400,
            )
        if request.speaker_profile != self.config.speaker_profile:
            raise TtsServiceError(
                "speaker_profile_mismatch",
                "Speaker profile does not match the active model bundle",
                status_code=409,
            )

    async def _get_backend(self) -> tuple[TtsBackend, float]:
        if self._status == "failed" and self._backend is None:
            raise TtsServiceError(
                "tts_artifacts_invalid",
                "TTS runtime artifacts failed startup validation",
                status_code=503,
            )
        if self._backend is not None:
            return self._backend, 0.0
        async with self._load_lock:
            if self._backend is not None:
                return self._backend, 0.0
            self._status = "loading"
            try:
                load_start = time.perf_counter()
                self._backend = await asyncio.to_thread(self._runtime_loader, self.config)
                load_ms = (time.perf_counter() - load_start) * 1000
                self._status = "ready"
                self._last_error = None
                return self._backend, load_ms
            except Exception as exc:
                self._status = "failed"
                self._last_error = type(exc).__name__
                self._failure_count += 1
                raise TtsServiceError("tts_load_failed", "TTS engine failed to load", status_code=503) from exc

    async def _admit(self) -> None:
        async with self._admission_lock:
            capacity = 1 + self.config.max_queue_depth
            if self._admitted >= capacity:
                raise TtsServiceError("tts_queue_full", "TTS queue is full", status_code=429, retryable=True)
            self._admitted += 1
            if self._admitted > 1:
                self._waiting += 1


def _load_openvino_backend(config: TtsServiceConfig) -> TtsBackend:
    if config.mode != "cpu_reference":
        raise RuntimeError("mixed NPU/CPU mode has not passed the static-cache gate")

    import openvino as ov
    from transformers import BertTokenizerFast

    paths = TtsArtifactPaths.from_bundle(config.model_dir, config.speaker_profile)
    validate_bundle(paths)
    core = ov.Core()
    compile_properties: dict[str, Any] = {"INFERENCE_PRECISION_HINT": "f32"}
    if config.cpu_threads is not None:
        compile_properties["INFERENCE_NUM_THREADS"] = config.cpu_threads

    def compile_model(name: str):
        path = paths.tts_dir / name
        return core.compile_model(core.read_model(path), "CPU", compile_properties)

    models = TtsCompiledModels(
        text_embeddings=compile_model("openvino_tts_text_embeddings_model.xml"),
        audio_embeddings=compile_model("openvino_tts_audio_embeddings_model.xml"),
        transformer=compile_model("openvino_tts_transformer_model.xml"),
        dvae=compile_model("openvino_tts_dvae_model.xml"),
        vocos=compile_model("openvino_tts_vocos_model.xml"),
    )
    tokenizer = BertTokenizerFast.from_pretrained(paths.tokenizer_dir, local_files_only=True)
    speaker = np.load(paths.speaker_npy, allow_pickle=False)
    return _OpenVINOBackend(MiniCPMOTtsRuntime(models), tokenizer, speaker)


def os_urandom(length: int) -> bytes:
    import os

    return os.urandom(length)
