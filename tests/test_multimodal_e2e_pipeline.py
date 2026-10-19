"""End-to-End Integration Test for LIARA Multimodal Perception, Assembly, and Speech Pipeline."""

import base64
import numpy as np
import pytest

from services.contracts.multimodal import MultimodalPrefillInput, VisualFeatureBundle
from services.contracts.speech import AudioArtifact, SpeechPlan, SpeechPlanSegment, VoiceIdentity
from services.orchestrator.multimodal_assembly import MultimodalAssemblyEngine
from services.speech.kokoro_worker import KokoroSpeechWorker
from services.vision.openvino_vit_worker import OpenVINOVisionWorker
from services.vision.perception_buffer import VisualPerceptionBuffer


def test_multimodal_assembly_engine_grid_generation():
    """Verify 3D M-RoPE position grid construction [3, B, S_total]."""
    engine = MultimodalAssemblyEngine(hidden_dim=2048)

    np.random.seed(42)
    bundle_tokens = np.random.randn(64, 2048).astype(np.float16)
    b64_payload = base64.b64encode(bundle_tokens.tobytes()).decode("utf-8")

    bundle = VisualFeatureBundle(
        correlation_id="corr_assembly_01",
        num_tokens=64,
        hidden_dimension=2048,
        representation_space_id="qwen2.5_vl_2048d",
        tokens_base64=b64_payload,
        grid_thw=[1, 16, 16],
    )

    pos_grid = engine.generate_3d_mrope_grid(
        text_len_before=10,
        visual_bundles=[bundle],
        text_len_after=5,
        batch_size=1,
    )

    # Total length: 10 + 64 + 5 = 79
    assert pos_grid.shape == (3, 1, 79)
    assert pos_grid.dtype == np.int64


def test_multimodal_assembly_prefill_input_construction():
    """Verify text + visual token assembly into a validated MultimodalPrefillInput."""
    engine = MultimodalAssemblyEngine(hidden_dim=2048)

    text_before = np.random.randn(10, 2048).astype(np.float32)
    text_after = np.random.randn(5, 2048).astype(np.float32)

    vis_tokens = np.random.randn(64, 2048).astype(np.float16)
    b64_payload = base64.b64encode(vis_tokens.tobytes()).decode("utf-8")

    bundle = VisualFeatureBundle(
        correlation_id="corr_assembly_02",
        num_tokens=64,
        hidden_dimension=2048,
        representation_space_id="qwen2.5_vl_2048d",
        tokens_base64=b64_payload,
        grid_thw=[1, 16, 16],
    )

    prefill_input = engine.assemble_prefill_input(
        text_embeds_before=text_before,
        visual_bundles=[bundle],
        text_embeds_after=text_after,
        correlation_id="prefill_test_01",
    )

    assert isinstance(prefill_input, MultimodalPrefillInput)
    assert prefill_input.sequence_length == 79
    assert prefill_input.hidden_dimension == 2048
    assert prefill_input.contract == "multimodal_prefill_input/v1"

    # Decode unified matrix back to numpy
    unified_bytes = base64.b64decode(prefill_input.unified_tensor_base64)
    reconstructed_matrix = np.frombuffer(unified_bytes, dtype=np.float16).reshape(79, 2048)
    assert reconstructed_matrix.shape == (79, 2048)


def test_e2e_vision_gating_assembly_speech_pipeline():
    """Verify complete pipeline: Vision Worker -> Perception Gating -> Assembly -> Speech Output."""
    vit_worker = OpenVINOVisionWorker(device="CPU")
    gating_buffer = VisualPerceptionBuffer(trigger_threshold=0.98)
    assembly_engine = MultimodalAssemblyEngine(hidden_dim=2048)
    speech_worker = KokoroSpeechWorker(sample_rate=24000)

    # Frame 1: Camera image input
    np.random.seed(42)
    patches_f1 = np.random.randn(256, 1176).astype(np.float16)
    bundle_f1 = vit_worker.encode_patches(patches_f1, grid_thw=[1, 16, 16], correlation_id="f1")

    # Step 1: Perception Gating Buffer check
    should_trigger_f1, _ = gating_buffer.process_bundle(bundle_f1)
    assert should_trigger_f1 is True  # Frame 1 must trigger

    # Frame 2: Identical camera image input (static background)
    bundle_f2 = vit_worker.encode_patches(patches_f1, grid_thw=[1, 16, 16], correlation_id="f2")
    should_trigger_f2, sim_f2 = gating_buffer.process_bundle(bundle_f2)
    assert should_trigger_f2 is False  # Frame 2 must be DISCARDED (no LLM prefill)
    assert sim_f2 >= 0.99

    # Step 2: Assemble Multimodal Prefill Input for Frame 1
    text_before = np.random.randn(12, 2048).astype(np.float32)
    text_after = np.random.randn(4, 2048).astype(np.float32)

    prefill_input = assembly_engine.assemble_prefill_input(
        text_embeds_before=text_before,
        visual_bundles=[bundle_f1],
        text_embeds_after=text_after,
        correlation_id="e2e_prefill_01",
    )

    assert prefill_input.sequence_length == 80  # 12 + 64 + 4 = 80

    # Step 3: Generate Speech Response (Simulating LLM text response)
    speech_plan = SpeechPlan(
        voice_identity=VoiceIdentity(
            identity_id="de-female-kokoro",
            display_name="LIARA German Voice",
            qualities=["natural"],
            default_language="de-DE",
        ),
        segments=[
            SpeechPlanSegment(
                text="Starte den Docker Container auf OpenVINO.",
                semantic_role="sentence",
                pause_after_ms=150,
            )
        ],
    )

    audio_artifact = speech_worker.synthesize_plan(speech_plan, artifact_id="e2e_audio_out")

    assert isinstance(audio_artifact, AudioArtifact)
    assert audio_artifact.artifact_id == "e2e_audio_out"
    assert audio_artifact.sample_rate == 24000
    assert len(audio_artifact.url) > 50
