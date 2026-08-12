"""Format-independent contracts for LIARA speech planning and delivery."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class VoiceIdentity(BaseModel):
    """Stable DDNA description of how LIARA should sound."""

    model_config = ConfigDict(extra="forbid")

    identity_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    display_name: str = Field(min_length=1, max_length=100)
    qualities: list[str] = Field(min_length=1)
    default_language: str = Field(default="de-DE", pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$")


class SpeechProsody(BaseModel):
    """Engine-neutral guidance for one planned speech segment."""

    model_config = ConfigDict(extra="forbid")

    pace: Literal["slow", "measured", "natural", "brisk"] = "natural"
    emphasis: Literal["reduced", "neutral", "moderate", "strong"] = "neutral"
    tone: str | None = Field(default=None, max_length=80)


class SpeechPlanSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    semantic_role: Literal["sentence", "paragraph", "list_item", "quote"] = "sentence"
    pause_after_ms: int = Field(default=160, ge=0, le=2000)
    prosody: SpeechProsody = Field(default_factory=SpeechProsody)


class SpeechPlan(BaseModel):
    """Semantic plan between LIARA's identity and a TTS expression."""

    model_config = ConfigDict(extra="forbid")

    contract: Literal["speech_plan/v1"] = "speech_plan/v1"
    voice_identity: VoiceIdentity
    segments: list[SpeechPlanSegment] = Field(min_length=1)


class AudioArtifact(BaseModel):
    """Persistent encoded audio independent of any specific container."""

    model_config = ConfigDict(extra="forbid")

    contract: Literal["audio_artifact/v1"] = "audio_artifact/v1"
    artifact_id: str
    voice_identity_id: str
    media_type: str
    format: str
    url: str | None = None
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    duration_ms: int = Field(ge=0)


class AudioStream(BaseModel):
    """Negotiated descriptor for an ordered, cancellable audio stream."""

    model_config = ConfigDict(extra="forbid")

    contract: Literal["audio_stream/v1"] = "audio_stream/v1"
    stream_id: str
    voice_identity_id: str
    media_type: str
    codec: str
    sample_rate: int = Field(gt=0)
    channels: int = Field(gt=0)
    cancellable: bool = True
