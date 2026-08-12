"""Remote adapter for LIARA's internal OpenVINO TTS capability."""

from __future__ import annotations

import hashlib
import io
import os
import re
import wave
from dataclasses import dataclass
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from services.contracts import ChatArtifact, TtsGenerationRequest, TtsHealthResponse
from services.shared.sandboxing import (
    canonicalize_sandbox_root,
    get_global_sandbox_root,
    resolve_sandbox_root,
)


class TtsAdapterError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class TtsAudioResult:
    wav_bytes: bytes
    request_id: str
    audio_tokens: int
    sample_rate: int
    duration_ms: int
    mode: str
    server_timing: str


@dataclass
class TtsAudioStreamResult:
    request_id: str
    media_type: str
    codec: str
    sample_rate: int
    channels: int
    mode: str
    response: httpx.Response
    client: httpx.AsyncClient
    max_response_bytes: int

    async def iter_bytes(self) -> AsyncIterator[bytes]:
        received = 0
        async for chunk in self.response.aiter_bytes():
            if not chunk:
                continue
            received += len(chunk)
            if received > self.max_response_bytes:
                raise TtsAdapterError(
                    "tts_response_too_large", "TTS stream exceeds the adapter limit"
                )
            yield chunk

    async def aclose(self) -> None:
        await self.response.aclose()
        await self.client.aclose()


@dataclass
class PcmStreamArtifactSink:
    """Incrementally persist PCM frames as a session-scoped WAV on success."""

    target: Any
    temporary: Any
    url: str
    sample_rate: int = 24_000
    _writer: Any = None
    _frames: int = 0
    _closed: bool = False
    _pending_byte: bytes = b""

    def write(self, pcm_bytes: bytes) -> None:
        if self._closed:
            raise RuntimeError("PCM artifact sink is already closed")
        if not pcm_bytes:
            return
        pcm_bytes = self._pending_byte + pcm_bytes
        if len(pcm_bytes) % 2:
            self._pending_byte = pcm_bytes[-1:]
            pcm_bytes = pcm_bytes[:-1]
        else:
            self._pending_byte = b""
        if not pcm_bytes:
            return
        if self._writer is None:
            self.temporary.parent.mkdir(parents=True, exist_ok=True)
            self._writer = wave.open(str(self.temporary), "wb")
            self._writer.setnchannels(1)
            self._writer.setsampwidth(2)
            self._writer.setframerate(self.sample_rate)
        self._writer.writeframesraw(pcm_bytes)
        self._frames += len(pcm_bytes) // 2

    def commit(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pending_byte:
            self.abort()
            raise RuntimeError("Cannot commit a speech artifact with an incomplete PCM16 frame")
        if self._writer is None or self._frames <= 0:
            self.abort()
            raise RuntimeError("Cannot commit an empty speech stream artifact")
        self._writer.close()
        self._writer = None
        os.replace(self.temporary, self.target)
        try:
            with wave.open(str(self.target), "rb") as wav:
                valid = (
                    wav.getnchannels() == 1
                    and wav.getsampwidth() == 2
                    and wav.getframerate() == self.sample_rate
                    and wav.getnframes() == self._frames
                )
        except (EOFError, wave.Error):
            valid = False
        if not valid:
            self.target.unlink(missing_ok=True)
            raise RuntimeError("Persisted speech stream artifact failed WAV verification")

    def abort(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        self._closed = True
        self._pending_byte = b""
        self.temporary.unlink(missing_ok=True)


def prepare_pcm_stream_artifact(
    *,
    session_id: str,
    sandbox_root: str | None = None,
) -> PcmStreamArtifactSink:
    """Prepare a controlled on-complete WAV target without creating a public partial file."""
    canonical_root = canonicalize_sandbox_root(sandbox_root)
    local_root = resolve_sandbox_root(canonical_root, get_global_sandbox_root())
    safe_session = _safe_segment(session_id, fallback="session")
    filename = f"tts-stream-{uuid4().hex}.wav"
    artifact_root = (local_root / ".liara_artifacts" / safe_session).resolve()
    target = (artifact_root / filename).resolve()
    target.relative_to(artifact_root)
    temporary = target.with_suffix(".wav.part")
    relative_path = target.relative_to(local_root).as_posix()
    normalized_root = canonical_root.replace("\\", "/").rstrip("/")
    stored_path = f"{normalized_root}/{relative_path}"
    url = (
        f"/files/artifact?session_id={quote(session_id, safe='')}"
        f"&path={quote(stored_path, safe='')}"
    )
    return PcmStreamArtifactSink(target=target, temporary=temporary, url=url)


class TtsServiceAdapter:
    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8040",
        timeout_seconds: float = 360.0,
        max_response_bytes: int = 25 * 1024 * 1024,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.transport = transport

    async def health(self) -> TtsHealthResponse:
        response = await self._request("GET", "/tts/health")
        self._raise_for_error(response)
        return TtsHealthResponse.model_validate(response.json())

    async def generate(self, request: TtsGenerationRequest) -> TtsAudioResult:
        response = await self._request("POST", "/tts/generate", json_body=request.model_dump())
        self._raise_for_error(response)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type != "audio/wav":
            raise TtsAdapterError("invalid_tts_response", "TTS service did not return audio/wav")
        if len(response.content) > self.max_response_bytes:
            raise TtsAdapterError("tts_response_too_large", "TTS response exceeds the adapter limit")

        try:
            with wave.open(io.BytesIO(response.content), "rb") as wav:
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                sample_rate = wav.getframerate()
                frames = wav.getnframes()
        except (EOFError, wave.Error) as exc:
            raise TtsAdapterError("invalid_tts_response", "TTS service returned an invalid WAV") from exc
        if channels != 1 or sample_width != 2 or frames <= 0:
            raise TtsAdapterError("invalid_tts_response", "TTS WAV must be non-empty mono PCM16")

        header_sample_rate = _required_int_header(response, "X-Liara-TTS-Sample-Rate")
        duration_ms = _required_int_header(response, "X-Liara-TTS-Duration-Ms")
        expected_duration_ms = round(frames * 1000 / sample_rate)
        if sample_rate != header_sample_rate or duration_ms != expected_duration_ms:
            raise TtsAdapterError("invalid_tts_response", "TTS WAV metadata does not match its headers")
        return TtsAudioResult(
            wav_bytes=response.content,
            request_id=_required_header(response, "X-Liara-TTS-Request-Id"),
            audio_tokens=_required_int_header(response, "X-Liara-TTS-Audio-Tokens"),
            sample_rate=sample_rate,
            duration_ms=duration_ms,
            mode=_required_header(response, "X-Liara-TTS-Mode"),
            server_timing=response.headers.get("Server-Timing", ""),
        )

    async def open_stream(self, request: TtsGenerationRequest) -> TtsAudioStreamResult:
        client = self._client()
        response: httpx.Response | None = None
        try:
            outbound = client.build_request("POST", "/tts/stream", json=request.model_dump())
            response = await client.send(outbound, stream=True)
            if not response.is_success:
                await response.aread()
                self._raise_for_error(response)
            media_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if media_type != "audio/x-pcm":
                raise TtsAdapterError(
                    "invalid_tts_response", "TTS service did not return a PCM stream"
                )
            if _required_header(response, "X-Liara-TTS-Stream-Contract") != "audio_stream/v1":
                raise TtsAdapterError(
                    "invalid_tts_response", "TTS service returned an unknown stream contract"
                )
            codec = _required_header(response, "X-Liara-TTS-Codec")
            sample_rate = _required_int_header(response, "X-Liara-TTS-Sample-Rate")
            channels = _required_int_header(response, "X-Liara-TTS-Channels")
            if codec != "pcm_s16le" or sample_rate != 24_000 or channels != 1:
                raise TtsAdapterError(
                    "invalid_tts_response", "TTS PCM stream parameters are unsupported"
                )
            return TtsAudioStreamResult(
                request_id=_required_header(response, "X-Liara-TTS-Request-Id"),
                media_type=response.headers.get("content-type", "audio/x-pcm"),
                codec=codec,
                sample_rate=sample_rate,
                channels=channels,
                mode=_required_header(response, "X-Liara-TTS-Mode"),
                response=response,
                client=client,
                max_response_bytes=self.max_response_bytes,
            )
        except httpx.TimeoutException as exc:
            if response is not None:
                await response.aclose()
            await client.aclose()
            raise TtsAdapterError(
                "tts_timeout", "TTS service request timed out", status_code=504, retryable=True
            ) from exc
        except httpx.RequestError as exc:
            if response is not None:
                await response.aclose()
            await client.aclose()
            raise TtsAdapterError(
                "tts_unavailable", "TTS service is unavailable", status_code=503, retryable=True
            ) from exc
        except Exception:
            if response is not None:
                await response.aclose()
            await client.aclose()
            raise

    async def generate_artifact(
        self,
        request: TtsGenerationRequest,
        *,
        session_id: str,
        sandbox_root: str | None = None,
        title: str = "LIARA speech",
    ) -> ChatArtifact:
        audio = await self.generate(request)
        artifact = persist_tts_chat_artifact(
            audio,
            session_id=session_id,
            sandbox_root=sandbox_root,
            title=title,
        )
        return attach_tts_artifact_url(artifact)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout_seconds),
            transport=self.transport,
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            async with self._client() as client:
                return await client.request(method, path, json=json_body)
        except httpx.TimeoutException as exc:
            raise TtsAdapterError(
                "tts_timeout",
                "TTS service request timed out",
                status_code=504,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise TtsAdapterError(
                "tts_unavailable",
                "TTS service is unavailable",
                status_code=503,
                retryable=True,
            ) from exc

    @staticmethod
    def _raise_for_error(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            payload: dict[str, Any] = response.json()
        except ValueError:
            payload = {}
        raise TtsAdapterError(
            str(payload.get("code") or "tts_service_error"),
            str(payload.get("message") or "TTS service request failed"),
            status_code=response.status_code,
            retryable=bool(payload.get("retryable", False)),
        )


def persist_tts_chat_artifact(
    audio: TtsAudioResult,
    *,
    session_id: str,
    sandbox_root: str | None = None,
    title: str = "LIARA speech",
) -> ChatArtifact:
    """Persist WAV bytes inside the API's existing session artifact boundary."""
    canonical_root = canonicalize_sandbox_root(sandbox_root)
    local_root = resolve_sandbox_root(canonical_root, get_global_sandbox_root())
    safe_session = _safe_segment(session_id, fallback="session")
    filename = f"tts-{uuid4().hex}.wav"
    artifact_root = (local_root / ".liara_artifacts" / safe_session).resolve()
    target = (artifact_root / filename).resolve()
    target.relative_to(artifact_root)
    artifact_root.mkdir(parents=True, exist_ok=True)

    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(audio.wav_bytes)
    os.replace(temporary, target)
    persisted = target.read_bytes()
    if persisted != audio.wav_bytes:
        target.unlink(missing_ok=True)
        raise RuntimeError("TTS artifact read-back verification failed")

    relative_path = target.relative_to(local_root).as_posix()
    normalized_root = canonical_root.replace("\\", "/").rstrip("/")
    stored_path = f"{normalized_root}/{relative_path}"
    return ChatArtifact(
        kind="audio",
        mime_type="audio/wav",
        title=title,
        source_tool="tts_service",
        metadata={
            "contract": "audio_artifact/v1",
            "voice_identity_id": "liara",
            "format": "wav",
            "channels": 1,
            "stored_path": stored_path,
            "stored_path_local": str(target),
            "session_id": session_id,
            "request_id": audio.request_id,
            "sha256": hashlib.sha256(persisted).hexdigest(),
            "size_bytes": len(persisted),
            "audio_tokens": audio.audio_tokens,
            "sample_rate": audio.sample_rate,
            "duration_ms": audio.duration_ms,
            "mode": audio.mode,
        },
    )


def attach_tts_artifact_url(artifact: ChatArtifact) -> ChatArtifact:
    """Attach the API's existing session-scoped download URL to a TTS artifact."""
    metadata = artifact.metadata or {}
    stored_path = str(metadata.get("stored_path") or "").strip()
    session_id = str(metadata.get("session_id") or "").strip()
    if not stored_path or not session_id:
        raise ValueError("TTS artifact requires stored_path and session_id metadata")
    return artifact.model_copy(
        update={
            "url": (
                f"/files/artifact?session_id={quote(session_id, safe='')}"
                f"&path={quote(stored_path, safe='')}"
            )
        }
    )


def _required_header(response: httpx.Response, name: str) -> str:
    value = response.headers.get(name, "").strip()
    if not value:
        raise TtsAdapterError("invalid_tts_response", f"TTS response is missing {name}")
    return value


def _required_int_header(response: httpx.Response, name: str) -> int:
    try:
        return int(_required_header(response, name))
    except ValueError as exc:
        raise TtsAdapterError("invalid_tts_response", f"TTS response has invalid {name}") from exc


def _safe_segment(value: str, *, fallback: str) -> str:
    candidate = re.sub(r"[^a-zA-Z0-9._-]+", "_", (value or fallback).strip())
    return candidate.strip("._") or fallback
