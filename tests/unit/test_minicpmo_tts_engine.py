from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import numpy as np
import pytest

from services.contracts import TtsGenerationRequest
from services.inference.minicpmo_tts.config import TtsServiceConfig
from services.inference.minicpmo_tts.engine import OpenVINOTtsEngine, TtsServiceError
from services.inference.minicpmo_tts.runtime import TtsRuntimeResult


def _config(*, enabled: bool = True, max_queue_depth: int = 2) -> TtsServiceConfig:
    return TtsServiceConfig(
        enabled=enabled,
        model_dir=Path("C:/fake/model"),
        mode="cpu_reference",
        speaker_profile="neutral-v1",
        max_text_chars=2000,
        max_audio_tokens=400,
        request_timeout_seconds=2.0,
        queue_timeout_seconds=1.0,
        max_queue_depth=max_queue_depth,
        cpu_threads=None,
        cache_dir=Path("C:/fake/cache"),
    )


class _FakeBackend:
    def generate(self, text, max_audio_tokens, seed, cancelled):
        assert not cancelled()
        generated_tokens = max_audio_tokens - 1
        codes = np.full((1, 4, generated_tokens), seed % 7, dtype=np.int64)
        waveform = np.linspace(-0.25, 0.25, generated_tokens * 512, dtype=np.float32)[None, :]
        return TtsRuntimeResult(waveform, codes, 24_000, {"generate": 1.0})


class _CapturingBackend(_FakeBackend):
    def __init__(self):
        self.texts = []

    def generate(self, text, max_audio_tokens, seed, cancelled):
        self.texts.append(text)
        generated_tokens = max_audio_tokens if len(text) > 36 else max_audio_tokens - 1
        assert not cancelled()
        codes = np.full((1, 4, generated_tokens), seed % 7, dtype=np.int64)
        waveform = np.linspace(-0.25, 0.25, generated_tokens * 512, dtype=np.float32)[None, :]
        return TtsRuntimeResult(waveform, codes, 24_000, {"generate": 1.0})


@pytest.mark.asyncio
async def test_engine_health_does_not_load_disabled_backend():
    loads = 0

    def loader(config):
        nonlocal loads
        loads += 1
        return _FakeBackend()

    engine = OpenVINOTtsEngine(_config(enabled=False), loader)

    assert engine.health().status == "disabled"
    assert engine.health().loaded is False
    assert loads == 0
    with pytest.raises(TtsServiceError, match="disabled"):
        await engine.generate(TtsGenerationRequest(text="Hallo"))
    assert loads == 0


@pytest.mark.asyncio
async def test_enabled_engine_fails_startup_validation_before_model_load(tmp_path):
    config = _config()
    config = config.__class__(
        **{**config.__dict__, "model_dir": tmp_path}
    )

    engine = OpenVINOTtsEngine(config)

    assert engine.health().status == "failed"
    assert engine.health().loaded is False
    assert engine.health().last_error == "TtsArtifactError"
    with pytest.raises(TtsServiceError) as exc_info:
        await engine.generate(TtsGenerationRequest(text="Hallo"))
    assert exc_info.value.code == "tts_artifacts_invalid"


@pytest.mark.asyncio
async def test_engine_loads_once_and_reuses_backend():
    loads = 0

    def loader(config):
        nonlocal loads
        loads += 1
        time.sleep(0.02)
        return _FakeBackend()

    engine = OpenVINOTtsEngine(_config(), loader)
    first, second = await __import__("asyncio").gather(
        engine.generate(TtsGenerationRequest(text="Eins", max_audio_tokens=25, seed=3)),
        engine.generate(TtsGenerationRequest(text="Zwei", max_audio_tokens=25, seed=4)),
    )

    assert loads == 1
    assert first.audio_tokens == second.audio_tokens == 24
    assert first.wav_bytes.startswith(b"RIFF")
    assert engine.health().status == "ready"
    assert engine.health().request_count == 2


@pytest.mark.asyncio
async def test_engine_segments_long_form_text_and_combines_all_audio():
    backend = _CapturingBackend()
    engine = OpenVINOTtsEngine(_config(), lambda config: backend)
    text = (
        "- Liara ist ein AI-Orchestrator und Agent.\n"
        "- Alle konfigurierten Backends werden als healthy gemeldet [TOOL:sys].\n"
        "- Der Embedding-Runtime laeuft auf einer NPU.\n"
        "**Annahmen** Da keine Fehlermeldungen vorliegen, wird angenommen, dass Liara betriebsbereit ist."
    )

    result = await engine.generate(
        TtsGenerationRequest(text=text, max_audio_tokens=25, seed=11)
    )

    assert len(backend.texts) >= 2
    assert all("[TOOL:" not in segment for segment in backend.texts)
    assert all(not segment.startswith("-") for segment in backend.texts)
    accepted_segments = int(result.timings_ms["segment_count"])
    assert len(backend.texts) >= accepted_segments
    assert result.audio_tokens < accepted_segments * 25
    assert result.timings_ms["segment_count"] == accepted_segments
    expected_samples = (result.audio_tokens * 512) + round(
        24_000 * result.timings_ms["planned_pause_ms"] / 1000
    )
    assert len(result.wav_bytes) == 44 + expected_samples * 2


@pytest.mark.asyncio
async def test_engine_stream_is_ordered_and_only_generates_on_demand():
    backend = _CapturingBackend()
    engine = OpenVINOTtsEngine(_config(), lambda config: backend)
    stream = await engine.stream(
        TtsGenerationRequest(
            text="- Der erste Abschnitt.\n- Der zweite Abschnitt.",
            max_audio_tokens=25,
            seed=11,
        )
    )

    first = await anext(stream)
    assert first.sequence == 0
    assert first.kind == "audio"
    assert first.pcm_bytes
    assert len(backend.texts) == 1

    pause = await anext(stream)
    assert pause.sequence == 1
    assert pause.kind == "pause"
    assert pause.duration_ms == 220
    assert set(pause.pcm_bytes) == {0}
    assert len(backend.texts) == 1

    second = await anext(stream)
    assert second.sequence == 2
    assert second.kind == "audio"
    assert len(backend.texts) == 2
    await stream.aclose()


@pytest.mark.asyncio
async def test_engine_stream_cancellation_reaches_running_backend():
    started = threading.Event()
    cancelled_seen = threading.Event()

    class CancellableBackend(_FakeBackend):
        def generate(self, text, max_audio_tokens, seed, cancelled):
            started.set()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if cancelled():
                    cancelled_seen.set()
                    raise RuntimeError("cancelled")
                time.sleep(0.005)
            raise RuntimeError("cancellation was not propagated")

    engine = OpenVINOTtsEngine(_config(), lambda config: CancellableBackend())
    stream = await engine.stream(TtsGenerationRequest(text="Abbruchtest", max_audio_tokens=25))
    consume = asyncio.create_task(anext(stream))
    assert await asyncio.to_thread(started.wait, 1.0)
    consume.cancel()
    with pytest.raises(asyncio.CancelledError):
        await consume
    assert await asyncio.to_thread(cancelled_seen.wait, 1.0)
    assert engine.health().queue_depth == 0


@pytest.mark.asyncio
async def test_engine_rejects_irreducibly_capped_segment():
    class CappedBackend(_FakeBackend):
        def generate(self, text, max_audio_tokens, seed, cancelled):
            result = super().generate(text, max_audio_tokens + 1, seed, cancelled)
            assert result.audio_codes.shape[2] == max_audio_tokens
            return result

    engine = OpenVINOTtsEngine(_config(), lambda config: CappedBackend())

    with pytest.raises(TtsServiceError) as exc_info:
        await engine.generate(TtsGenerationRequest(text="Kurzer Satz.", max_audio_tokens=25))

    assert exc_info.value.code == "tts_segment_truncated"


@pytest.mark.asyncio
async def test_engine_rejects_request_when_queue_capacity_is_exhausted():
    started = threading.Event()
    release = threading.Event()

    class BlockingBackend(_FakeBackend):
        def generate(self, text, max_audio_tokens, seed, cancelled):
            started.set()
            assert release.wait(timeout=1.0)
            return super().generate(text, max_audio_tokens, seed, cancelled)

    engine = OpenVINOTtsEngine(_config(max_queue_depth=0), lambda config: BlockingBackend())
    first = asyncio.create_task(engine.generate(TtsGenerationRequest(text="Eins")))
    assert await asyncio.to_thread(started.wait, 1.0)

    with pytest.raises(TtsServiceError) as exc_info:
        await engine.generate(TtsGenerationRequest(text="Zwei"))
    assert exc_info.value.code == "tts_queue_full"
    assert exc_info.value.status_code == 429

    release.set()
    await first
