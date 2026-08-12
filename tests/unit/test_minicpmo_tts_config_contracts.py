from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.contracts import TtsGenerationRequest
from services.inference.minicpmo_tts.config import TtsServiceConfig


def test_tts_config_is_disabled_cpu_reference_by_default(monkeypatch):
    for name in (
        "OPENVINO_TTS_ENABLED",
        "OPENVINO_TTS_MODE",
        "OPENVINO_TTS_MAX_QUEUE_DEPTH",
    ):
        monkeypatch.delenv(name, raising=False)

    config = TtsServiceConfig.from_env()

    assert config.enabled is False
    assert config.mode == "cpu_reference"
    assert config.max_queue_depth == 2


def test_tts_config_rejects_client_unsafe_mode(monkeypatch):
    monkeypatch.setenv("OPENVINO_TTS_MODE", "NPU")

    with pytest.raises(ValueError, match="OPENVINO_TTS_MODE"):
        TtsServiceConfig.from_env()


def test_tts_request_enforces_public_limits():
    request = TtsGenerationRequest(text="Hallo", seed=2**32 - 1)

    assert request.speaker_profile == "neutral-v1"
    assert request.max_audio_tokens == 100

    with pytest.raises(ValidationError):
        TtsGenerationRequest(text="x", max_audio_tokens=24)
    with pytest.raises(ValidationError):
        TtsGenerationRequest(text="x", device_override="NPU")