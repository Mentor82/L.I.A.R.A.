"""Format-independent contracts for LIARA multimodal feature bundles and prefill states."""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


class VisualFeatureBundle(BaseModel):
    """Model-independent container for visual perception features projected into LLM token space."""

    model_config = ConfigDict(extra="forbid")

    contract: Literal["visual_feature_bundle/v1"] = "visual_feature_bundle/v1"
    correlation_id: str = Field(min_length=1)
    producer_id: str = Field(default="openvino_vit_worker")
    representation_space_id: str = Field(default="qwen2.5_vl_2048d")
    dtype: str = Field(default="float16")
    num_tokens: int = Field(gt=0)
    hidden_dimension: int = Field(gt=0)
    tokens_base64: str = Field(min_length=1, description="Base64 encoded binary tensor payload")
    grid_thw: List[int] = Field(min_length=3, max_length=3, description="[T, H, W] grid dimensions")
    device_metadata: Dict[str, Any] = Field(default_factory=dict)


class AudioFeatureBundle(BaseModel):
    """Model-independent container for acoustic perception features."""

    model_config = ConfigDict(extra="forbid")

    contract: Literal["audio_feature_bundle/v1"] = "audio_feature_bundle/v1"
    correlation_id: str = Field(min_length=1)
    producer_id: str = Field(default="openvino_audio_worker")
    representation_space_id: str = Field(default="whisper_large_v3_1280d")
    dtype: str = Field(default="float16")
    num_frames: int = Field(gt=0)
    hidden_dimension: int = Field(gt=0)
    features_base64: str = Field(min_length=1, description="Base64 encoded binary tensor payload")
    sample_rate: int = Field(default=16000, gt=0)
    duration_ms: int = Field(ge=0)
    device_metadata: Dict[str, Any] = Field(default_factory=dict)


class MultimodalPrefillInput(BaseModel):
    """Unified context tensor ready for the LLM prefill pass."""

    model_config = ConfigDict(extra="forbid")

    contract: Literal["multimodal_prefill_input/v1"] = "multimodal_prefill_input/v1"
    correlation_id: str = Field(min_length=1)
    batch_size: int = Field(default=1, gt=0)
    sequence_length: int = Field(gt=0)
    hidden_dimension: int = Field(gt=0)
    unified_tensor_base64: str = Field(min_length=1, description="Base64 encoded float16 tensor")
    position_ids_3d_base64: str = Field(min_length=1, description="Base64 encoded int64 3D RoPE position IDs [3, B, S]")
    attention_mask_type: Literal["causal_2d", "full_2d"] = "causal_2d"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RepresentationSpaceMismatchError(ValueError):
    """Raised when a multimodal payload carries an incompatible representation space ID or dimension."""

    def __init__(self, actual_space: str, expected_space: str, message: str = ""):
        self.actual_space = actual_space
        self.expected_space = expected_space
        err_msg = message or f"Representation space mismatch: actual '{actual_space}' != expected '{expected_space}'"
        super().__init__(err_msg)


def validate_representation_space_compatibility(
    bundle: BaseModel,
    expected_space_id: str,
    expected_dim: Optional[int] = None,
) -> bool:
    """Fail-closed validator enforcing representation space compatibility."""
    actual_space = getattr(bundle, "representation_space_id", "")
    if actual_space != expected_space_id:
        raise RepresentationSpaceMismatchError(
            actual_space=actual_space,
            expected_space=expected_space_id,
            message=f"Fail-closed: Incompatible representation space '{actual_space}'. Pipeline expects '{expected_space_id}'."
        )

    if expected_dim is not None:
        actual_dim = getattr(bundle, "hidden_dimension", None)
        if actual_dim != expected_dim:
            raise RepresentationSpaceMismatchError(
                actual_space=actual_space,
                expected_space=expected_space_id,
                message=f"Fail-closed: Incompatible hidden dimension {actual_dim}. Pipeline expects {expected_dim}d."
            )

    return True
