from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.contracts import (
    AudioArtifact,
    AudioStream,
    SpeechPlan,
    SpeechPlanSegment,
    VoiceIdentity,
)


def test_liara_voice_identity_is_valid_and_runtime_independent():
    path = Path("config/ddna/liara-voice-identity.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    identity = VoiceIdentity.model_validate(payload)

    assert identity.identity_id == "liara"
    assert {"gentle", "warm", "calm", "articulate"} <= set(identity.qualities)
    assert not ({"codec", "format", "sample_rate", "backend", "speaker_profile"} & payload.keys())


def test_speech_plan_carries_identity_and_semantic_guidance():
    identity = VoiceIdentity.model_validate_json(
        Path("config/ddna/liara-voice-identity.json").read_text(encoding="utf-8")
    )

    plan = SpeechPlan(
        voice_identity=identity,
        segments=[
            SpeechPlanSegment(
                text="LIARA antwortet ruhig.",
                semantic_role="sentence",
                pause_after_ms=180,
            )
        ],
    )

    assert plan.contract == "speech_plan/v1"
    assert plan.segments[0].prosody.pace == "natural"


def test_audio_delivery_contracts_are_format_independent():
    artifact = AudioArtifact(
        artifact_id="artifact-1",
        voice_identity_id="liara",
        media_type="audio/wav",
        format="wav",
        sample_rate=24_000,
        channels=1,
        duration_ms=1000,
    )
    stream = AudioStream(
        stream_id="stream-1",
        voice_identity_id="liara",
        media_type="audio/webm; codecs=opus",
        codec="opus",
        sample_rate=48_000,
        channels=1,
    )

    assert artifact.contract == "audio_artifact/v1"
    assert stream.contract == "audio_stream/v1"
    assert stream.cancellable is True

    with pytest.raises(ValidationError):
        VoiceIdentity(
            identity_id="liara",
            display_name="LIARA",
            qualities=["calm"],
            codec="opus",
        )