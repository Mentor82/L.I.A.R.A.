"""Canonical contracts for LIARA's visual perception path."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator


VisionTask = Literal["question", "describe", "ocr", "analyze"]


class VisionImageInput(BaseModel):
    """A normalized image supplied to the trusted vision service boundary."""

    image_id: str = Field(min_length=1)
    media_type: Literal["image/jpeg", "image/png", "image/webp", "image/bmp"]
    content_base64: str = Field(min_length=1)
    sha256: str = Field(min_length=64, max_length=64)
    width: Optional[int] = Field(default=None, ge=1)
    height: Optional[int] = Field(default=None, ge=1)


class VisionRequest(BaseModel):
    """Model-independent request for one bounded visual observation."""

    request_id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    task: VisionTask = "question"
    images: List[VisionImageInput] = Field(min_length=1, max_length=4)
    max_tokens: int = Field(default=512, ge=1, le=2048)
    model: Optional[str] = None


class VisionImageEvidence(BaseModel):
    """Content identity of an image that actually reached the VLM."""

    image_id: str
    media_type: str
    sha256: str
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class VisionResponse(BaseModel):
    """Grounded visual observation returned by the perception service."""

    request_id: str
    status: Literal["success", "failed"]
    content: str = ""
    provider: str = "openvino"
    model: str
    device: str
    evidence: List[VisionImageEvidence] = Field(default_factory=list)
    gen_ms: Optional[float] = Field(default=None, ge=0.0)
    load_ms: Optional[float] = Field(default=None, ge=0.0)
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_evidence_state(self) -> "VisionResponse":
        if self.status == "success" and (not self.content.strip() or not self.evidence):
            raise ValueError("successful vision responses require content and image evidence")
        return self
