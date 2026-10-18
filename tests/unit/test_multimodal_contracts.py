"""Clean-checkout coverage for the public multimodal contracts."""

import pytest
from pydantic import ValidationError

from services.contracts import AudioFeatureBundle, MultimodalPrefillInput, VisualFeatureBundle
from services.contracts.multimodal import (
    RepresentationSpaceMismatchError,
    validate_representation_space_compatibility,
)


@pytest.mark.parametrize("model,payload", [
    (VisualFeatureBundle, dict(correlation_id="visual", num_tokens=1,
        hidden_dimension=2048, tokens_base64="AAA=", grid_thw=[1, 1, 1])),
    (AudioFeatureBundle, dict(correlation_id="audio", num_frames=1,
        hidden_dimension=1280, features_base64="AAA=", duration_ms=1)),
    (MultimodalPrefillInput, dict(correlation_id="prefill", sequence_length=1,
        hidden_dimension=2048, unified_tensor_base64="AAA=",
        position_ids_3d_base64="AAA=")),
])
def test_public_contract_roundtrip_and_validation(model, payload):
    bundle = model(**payload)
    assert model.model_validate_json(bundle.model_dump_json()) == bundle
    for invalid in (
        {**payload, "hidden_dimension": 0},
        {**payload, "correlation_id": ""},
        {**payload, "unexpected": True},
    ):
        with pytest.raises(ValidationError):
            model(**invalid)


@pytest.mark.parametrize("space,dimension", [
    ("wrong-space", 2048),
    ("qwen2.5_vl_2048d", 1280),
])
def test_representation_mismatch_fails_closed(space, dimension):
    bundle = VisualFeatureBundle(correlation_id="visual", num_tokens=1,
        hidden_dimension=2048, tokens_base64="AAA=", grid_thw=[1, 1, 1])
    assert validate_representation_space_compatibility(
        bundle, "qwen2.5_vl_2048d", 2048)
    with pytest.raises(RepresentationSpaceMismatchError):
        validate_representation_space_compatibility(bundle, space, dimension)
