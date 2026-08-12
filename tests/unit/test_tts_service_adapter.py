from __future__ import annotations

import io
import wave

import httpx
import pytest

from services.contracts import TtsGenerationRequest
from services.inference.tts_adapter import (
    TtsAdapterError,
    TtsServiceAdapter,
    prepare_pcm_stream_artifact,
)


def _wav_bytes(frames: int = 12800) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(b"\0\0" * frames)
    return output.getvalue()


@pytest.mark.asyncio
async def test_tts_adapter_validates_binary_contract_and_persists_chat_artifact(tmp_path, monkeypatch):
    payload = _wav_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tts/generate"
        assert "device_override" not in request.read().decode("utf-8")
        return httpx.Response(
            200,
            content=payload,
            headers={
                "Content-Type": "audio/wav",
                "X-Liara-TTS-Request-Id": "request-1",
                "X-Liara-TTS-Audio-Tokens": "25",
                "X-Liara-TTS-Sample-Rate": "24000",
                "X-Liara-TTS-Duration-Ms": "533",
                "X-Liara-TTS-Mode": "cpu_reference",
                "Server-Timing": "generate;dur=10.0",
            },
        )

    adapter = TtsServiceAdapter(transport=httpx.MockTransport(handler))
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")
    monkeypatch.setenv("LIARA_READ_ROOT", str(tmp_path))
    artifact = await adapter.generate_artifact(
        TtsGenerationRequest(text="Hello", speaker_profile="gentle-feminine-v1", max_audio_tokens=25),
        session_id="session/unsafe",
        sandbox_root=str(tmp_path),
    )

    stored = tmp_path / ".liara_artifacts" / "session_unsafe"
    files = list(stored.glob("*.wav"))
    assert len(files) == 1
    assert files[0].read_bytes() == payload
    assert artifact.kind == "audio"
    assert artifact.mime_type == "audio/wav"
    assert artifact.content_base64 is None
    assert artifact.metadata["contract"] == "audio_artifact/v1"
    assert artifact.metadata["voice_identity_id"] == "liara"
    assert artifact.metadata["format"] == "wav"
    assert artifact.metadata["channels"] == 1
    assert artifact.metadata["stored_path_local"] == str(files[0])
    assert artifact.metadata["request_id"] == "request-1"
    assert artifact.url is not None
    assert artifact.url.startswith("/files/artifact?session_id=session%2Funsafe&path=")
    assert "C%3A%2F" in artifact.url


@pytest.mark.asyncio
async def test_tts_adapter_preserves_structured_service_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"code": "tts_queue_full", "message": "TTS queue is full", "retryable": True},
        )

    adapter = TtsServiceAdapter(transport=httpx.MockTransport(handler))

    with pytest.raises(TtsAdapterError) as exc_info:
        await adapter.generate(TtsGenerationRequest(text="Hello"))
    assert exc_info.value.code == "tts_queue_full"
    assert exc_info.value.status_code == 429
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport_error", "expected_code", "expected_status"),
    [
        (httpx.ConnectError("refused"), "tts_unavailable", 503),
        (httpx.ReadTimeout("slow"), "tts_timeout", 504),
    ],
)
async def test_tts_adapter_maps_transport_errors(transport_error, expected_code, expected_status):
    def handler(request: httpx.Request) -> httpx.Response:
        transport_error.request = request
        raise transport_error

    adapter = TtsServiceAdapter(transport=httpx.MockTransport(handler))

    with pytest.raises(TtsAdapterError) as exc_info:
        await adapter.health()

    assert exc_info.value.code == expected_code
    assert exc_info.value.status_code == expected_status
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_tts_adapter_validates_and_forwards_pcm_stream():
    pcm = b"\x01\x00" * 256

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/tts/stream"
        return httpx.Response(
            200,
            content=pcm,
            headers={
                "Content-Type": "audio/x-pcm;format=s16le;rate=24000;channels=1",
                "X-Liara-TTS-Request-Id": "stream-request-1",
                "X-Liara-TTS-Stream-Contract": "audio_stream/v1",
                "X-Liara-TTS-Codec": "pcm_s16le",
                "X-Liara-TTS-Sample-Rate": "24000",
                "X-Liara-TTS-Channels": "1",
                "X-Liara-TTS-Mode": "cpu_reference",
            },
        )

    adapter = TtsServiceAdapter(transport=httpx.MockTransport(handler))
    stream = await adapter.open_stream(TtsGenerationRequest(text="Hello"))
    try:
        received = b"".join([chunk async for chunk in stream.iter_bytes()])
    finally:
        await stream.aclose()

    assert received == pcm
    assert stream.request_id == "stream-request-1"
    assert stream.codec == "pcm_s16le"
    assert stream.sample_rate == 24_000


def test_pcm_stream_artifact_sink_commits_only_a_complete_verified_wav(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")
    monkeypatch.setenv("LIARA_READ_ROOT", str(tmp_path))
    sink = prepare_pcm_stream_artifact(
        session_id="stream/session",
        sandbox_root=str(tmp_path),
    )

    payload = b"\x01\x00" * 128
    sink.write(payload[:127])
    sink.write(payload[127:])
    assert not sink.target.exists()
    sink.commit()

    assert sink.target.exists()
    assert not sink.temporary.exists()
    assert sink.url.startswith("/files/artifact?session_id=stream%2Fsession&path=")
    with wave.open(str(sink.target), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 24_000
        assert wav.getnframes() == 128


def test_pcm_stream_artifact_sink_discards_partial_output_on_abort(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")
    monkeypatch.setenv("LIARA_READ_ROOT", str(tmp_path))
    sink = prepare_pcm_stream_artifact(session_id="session-a", sandbox_root=str(tmp_path))

    sink.write(b"\x01\x00" * 16)
    sink.abort()

    assert not sink.target.exists()
    assert not sink.temporary.exists()
