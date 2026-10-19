"""Unit tests for LIARA Perception Gating Buffer & Fail-Closed Representation Space Validator."""

import base64
import numpy as np
import pytest

from services.contracts.multimodal import (
    RepresentationSpaceMismatchError,
    VisualFeatureBundle,
    validate_representation_space_compatibility,
)
from services.vision.perception_buffer import VisualPerceptionBuffer


def _create_dummy_bundle(
    correlation_id: str,
    space_id: str = "qwen2.5_vl_2048d",
    dim: int = 2048,
    seed: int = 42,
) -> VisualFeatureBundle:
    np.random.seed(seed)
    tokens = np.random.randn(64, dim).astype(np.float16)
    b64_payload = base64.b64encode(tokens.tobytes()).decode("utf-8")

    return VisualFeatureBundle(
        correlation_id=correlation_id,
        num_tokens=64,
        hidden_dimension=dim,
        representation_space_id=space_id,
        tokens_base64=b64_payload,
        grid_thw=[1, 16, 16],
    )


def test_perception_buffer_first_frame_triggers():
    """Verify first frame always triggers LLM execution."""
    buffer = VisualPerceptionBuffer(trigger_threshold=0.98)
    b1 = _create_dummy_bundle("f1", seed=42)

    should_trigger, sim = buffer.process_bundle(b1)
    assert should_trigger is True
    assert sim == 0.0
    assert buffer.total_llm_triggers == 1


def test_perception_buffer_discards_static_frames():
    """Verify static/similar frames are discarded (saving compute)."""
    buffer = VisualPerceptionBuffer(trigger_threshold=0.98)
    b1 = _create_dummy_bundle("f1", seed=42)
    b2 = _create_dummy_bundle("f2", seed=42)  # Identical seed -> identical content

    buffer.process_bundle(b1)
    should_trigger, sim = buffer.process_bundle(b2)

    assert should_trigger is False
    assert sim >= 0.99
    metrics = buffer.get_performance_metrics()
    assert metrics["total_frames_dropped"] == 1
    assert metrics["resource_savings_percent"] == 50.0


def test_perception_buffer_triggers_on_scene_change():
    """Verify significant scene change triggers downstream LLM."""
    buffer = VisualPerceptionBuffer(trigger_threshold=0.98)
    b1 = _create_dummy_bundle("f1", seed=42)
    b2 = _create_dummy_bundle("f2", seed=999)  # Different seed -> scene change

    buffer.process_bundle(b1)
    should_trigger, sim = buffer.process_bundle(b2)

    assert should_trigger is True
    assert sim < 0.5
    assert buffer.total_llm_triggers == 2


def test_fail_closed_representation_space_validation_pass():
    """Verify valid representation space ID and dimension pass validation."""
    bundle = _create_dummy_bundle("f1", space_id="qwen2.5_vl_2048d", dim=2048)
    assert validate_representation_space_compatibility(bundle, "qwen2.5_vl_2048d", 2048) is True


def test_fail_closed_representation_space_validation_fail():
    """Verify incompatible space ID or dimension raises RepresentationSpaceMismatchError."""
    bad_bundle = _create_dummy_bundle("f1", space_id="minicpm_1024d", dim=1024)

    with pytest.raises(RepresentationSpaceMismatchError) as exc_info:
        validate_representation_space_compatibility(bad_bundle, "qwen2.5_vl_2048d")

    assert "minicpm_1024d" in str(exc_info.value)
    assert "qwen2.5_vl_2048d" in str(exc_info.value)
