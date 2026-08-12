from __future__ import annotations

import io
import os
import wave
from pathlib import Path

import pytest

from services.contracts import TtsGenerationRequest
from services.inference.minicpmo_tts.config import TtsServiceConfig
from services.inference.minicpmo_tts.engine import OpenVINOTtsEngine


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OPENVINO_TTS_CPU") != "1",
    reason="set RUN_OPENVINO_TTS_CPU=1 to run the real OpenVINO TTS bundle",
)


@pytest.mark.asyncio
async def test_openvino_tts_cpu_generates_valid_wav():
    model_dir = Path(
        os.getenv(
            "OPENVINO_TTS_MODEL_DIR",
            "C:/ai/models/OpenVINO/MiniCPM-o-2.6-int4-sym-cw-ov",
        )
    )
    config = TtsServiceConfig(
        enabled=True,
        model_dir=model_dir,
        mode="cpu_reference",
        speaker_profile="neutral-v1",
        max_text_chars=2000,
        max_audio_tokens=400,
        request_timeout_seconds=180.0,
        queue_timeout_seconds=30.0,
        max_queue_depth=2,
        cpu_threads=None,
        cache_dir=Path("C:/ai/cache/openvino/minicpmo-tts"),
    )
    engine = OpenVINOTtsEngine(config)

    result = await engine.generate(
        TtsGenerationRequest(
            text="Hallo aus LIARA.",
            max_audio_tokens=25,
            seed=2606,
        )
    )

    with wave.open(io.BytesIO(result.wav_bytes), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert wav.getframerate() == 24_000
        assert wav.getnframes() == 12_800
    assert result.audio_tokens == 25
    assert result.duration_ms == 533
    assert engine.health().status == "ready"