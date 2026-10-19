"""Unit tests for KokoroSpeechWorker & LIARA Speech Contract compliance."""

import base64
import pytest

from services.contracts.speech import (
    AudioArtifact,
    AudioStream,
    SpeechPlan,
    SpeechPlanSegment,
    VoiceIdentity,
)
from services.speech.kokoro_worker import KokoroSpeechWorker


def test_kokoro_worker_initialization():
    """Verify KokoroSpeechWorker instantiation and default properties."""
    worker = KokoroSpeechWorker(device="CPU", sample_rate=24000)
    assert worker.target_device == "CPU"
    assert worker.sample_rate == 24000


def test_german_text_sanitization_and_code_switching():
    """Verify German G2P phonetization pre-processor preserves English technical terms."""
    worker = KokoroSpeechWorker()
    raw_text = "Starte den Docker Container und prüfe anschließend den Health Check."
    sanitized = worker.sanitize_phonemes_de(raw_text)

    assert "Doh-ker" in sanitized
    assert "Health-Check" in sanitized


def test_speech_plan_synthesis_to_audio_artifact():
    """Verify end-to-end synthesis from a SpeechPlan to a validated AudioArtifact."""
    worker = KokoroSpeechWorker(sample_rate=24000)

    voice_id = VoiceIdentity(
        identity_id="de-female-kokoro",
        display_name="LIARA German Voice",
        qualities=["clear", "calm", "natural"],
        default_language="de-DE",
    )

    plan = SpeechPlan(
        contract="speech_plan/v1",
        voice_identity=voice_id,
        segments=[
            SpeechPlanSegment(
                text="Starte den Docker Container.",
                semantic_role="sentence",
                pause_after_ms=200,
            ),
            SpeechPlanSegment(
                text="LIARA uses OpenVINO for local inference.",
                semantic_role="sentence",
                pause_after_ms=300,
            ),
        ],
    )

    artifact = worker.synthesize_plan(plan)

    assert isinstance(artifact, AudioArtifact)
    assert artifact.contract == "audio_artifact/v1"
    assert artifact.voice_identity_id == "de-female-kokoro"
    assert artifact.sample_rate == 24000
    assert artifact.format == "pcm16"
    assert artifact.duration_ms > 0

    # Verify base64 audio data payload
    prefix = "data:audio/pcm;base64,"
    assert artifact.url.startswith(prefix)
    b64_payload = artifact.url[len(prefix):]
    audio_bytes = base64.b64decode(b64_payload)
    assert len(audio_bytes) > 0


def test_audio_stream_descriptor_creation():
    """Verify creation of negotiated AudioStream contract."""
    worker = KokoroSpeechWorker(sample_rate=24000)
    stream = worker.create_stream_descriptor(voice_identity_id="de-female-kokoro")

    assert isinstance(stream, AudioStream)
    assert stream.contract == "audio_stream/v1"
    assert stream.voice_identity_id == "de-female-kokoro"
    assert stream.codec == "pcm16"
    assert stream.sample_rate == 24000
    assert stream.cancellable is True
