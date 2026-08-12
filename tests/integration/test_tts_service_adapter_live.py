from __future__ import annotations

import os

import pytest

from services.contracts import TtsGenerationRequest
from services.inference import TtsServiceAdapter


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_OPENVINO_TTS_ADAPTER_LIVE") != "1",
    reason="set RUN_OPENVINO_TTS_ADAPTER_LIVE=1 to call the live Port-8040 TTS service",
)


@pytest.mark.asyncio
async def test_tts_adapter_calls_live_cpu_service():
    adapter = TtsServiceAdapter(
        base_url=os.getenv("OPENVINO_TTS_BASE_URL", "http://127.0.0.1:8040")
    )

    health = await adapter.health()
    audio = await adapter.generate(
        TtsGenerationRequest(
            text="Hello from the LIARA adapter.",
            speaker_profile="gentle-feminine-v1",
            max_audio_tokens=25,
            seed=2606,
        )
    )

    assert health.status == "ready"
    assert health.mode == "cpu_reference"
    assert audio.wav_bytes.startswith(b"RIFF")
    assert audio.audio_tokens == 25
    assert audio.sample_rate == 24_000
    assert audio.duration_ms == 533