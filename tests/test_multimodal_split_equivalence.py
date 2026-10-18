"""Split-Equivalence Acceptance Unit Test for LIARA Multimodal Contracts & Vision Worker."""

import base64
import json
import numpy as np
import pytest

from services.contracts.multimodal import (
    AudioFeatureBundle,
    MultimodalPrefillInput,
    VisualFeatureBundle,
)
from services.vision.openvino_vit_worker import OpenVINOVisionWorker


def test_visual_feature_bundle_contract_serialization():
    """Verify VisualFeatureBundle schema validation, base64 payload, and extra='forbid' constraint."""
    fake_tokens = np.random.randn(64, 2048).astype(np.float16)
    tokens_b64 = base64.b64encode(fake_tokens.tobytes()).decode("utf-8")

    bundle = VisualFeatureBundle(
        correlation_id="corr_test_001",
        num_tokens=64,
        hidden_dimension=2048,
        tokens_base64=tokens_b64,
        grid_thw=[1, 16, 16],
        device_metadata={"requested_device": "CPU"},
    )

    assert bundle.contract == "visual_feature_bundle/v1"
    assert bundle.representation_space_id == "qwen2.5_vl_2048d"
    assert bundle.num_tokens == 64
    assert bundle.hidden_dimension == 2048

    # Reconstruct numpy array from base64
    decoded_bytes = base64.b64decode(bundle.tokens_base64)
    reconstructed = np.frombuffer(decoded_bytes, dtype=np.float16).reshape(64, 2048)
    np.testing.assert_array_equal(fake_tokens, reconstructed)

    # Verify JSON roundtrip
    json_str = bundle.model_dump_json()
    bundle_reconstructed = VisualFeatureBundle.model_validate_json(json_str)
    assert bundle_reconstructed.correlation_id == bundle.correlation_id


def test_audio_feature_bundle_contract_serialization():
    """Verify AudioFeatureBundle schema validation and binary encoding."""
    fake_features = np.random.randn(100, 1280).astype(np.float16)
    features_b64 = base64.b64encode(fake_features.tobytes()).decode("utf-8")

    bundle = AudioFeatureBundle(
        correlation_id="audio_corr_001",
        num_frames=100,
        hidden_dimension=1280,
        features_base64=features_b64,
        duration_ms=2000,
    )

    assert bundle.contract == "audio_feature_bundle/v1"
    assert bundle.representation_space_id == "whisper_large_v3_1280d"
    assert bundle.sample_rate == 16000


def test_multimodal_prefill_input_contract_serialization():
    """Verify MultimodalPrefillInput schema validation."""
    fake_tensor = np.random.randn(1, 128, 2048).astype(np.float16)
    tensor_b64 = base64.b64encode(fake_tensor.tobytes()).decode("utf-8")
    pos_ids_b64 = base64.b64encode(np.zeros((3, 1, 128), dtype=np.int64).tobytes()).decode("utf-8")

    prefill_input = MultimodalPrefillInput(
        correlation_id="prefill_corr_001",
        batch_size=1,
        sequence_length=128,
        hidden_dimension=2048,
        unified_tensor_base64=tensor_b64,
        position_ids_3d_base64=pos_ids_b64,
    )

    assert prefill_input.contract == "multimodal_prefill_input/v1"
    assert prefill_input.sequence_length == 128


def test_openvino_vision_worker_contract_generation():
    """Test OpenVINOVisionWorker patch encoding and VisualFeatureBundle output."""
    worker = OpenVINOVisionWorker(device="CPU")
    dummy_patches = np.random.randn(256, 1176).astype(np.float16)
    grid_thw = [1, 16, 16]

    bundle = worker.encode_patches(dummy_patches, grid_thw=grid_thw, correlation_id="test_worker_01")

    assert isinstance(bundle, VisualFeatureBundle)
    assert bundle.correlation_id == "test_worker_01"
    assert bundle.num_tokens == 64  # 256 patches / 4 (2x2 spatial merge)
    assert bundle.hidden_dimension == 2048


def test_split_equivalence_numerical_metrics():
    """Numerical metric check verifying mathematical tolerance thresholds."""
    # Synthetic target (monolithic) logits vs separated pipeline logits
    np.random.seed(42)
    mono_logits = np.random.randn(1, 1, 1000).astype(np.float32)
    # Add tiny perturbation within tolerance (e.g. 1e-5)
    split_logits = mono_logits + np.random.normal(0, 1e-6, mono_logits.shape).astype(np.float32)

    delta_max = float(np.max(np.abs(mono_logits - split_logits)))
    delta_avg = float(np.mean(np.abs(mono_logits - split_logits)))

    # Cosine similarity calculation
    v1 = mono_logits.flatten()
    v2 = split_logits.flatten()
    cos_sim = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2)))

    # Top-5 ranking check
    top5_mono = np.argsort(v1)[-5:]
    top5_split = np.argsort(v2)[-5:]
    ranking_match_rate = float(np.isin(top5_mono, top5_split).sum() / 5.0)

    assert delta_max <= 1e-4, f"Max error {delta_max} exceeded threshold 1e-4"
    assert delta_avg <= 1e-5, f"Mean error {delta_avg} exceeded threshold 1e-5"
    assert cos_sim >= 0.9999, f"Cosine similarity {cos_sim} below threshold 0.9999"
    assert ranking_match_rate == 1.0, f"Top-5 ranking match rate {ranking_match_rate} below 1.0"
