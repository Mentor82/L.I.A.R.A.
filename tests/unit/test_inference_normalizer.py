"""Unit tests for inference stream/final normalization."""

from services.contracts import InferenceResult
from services.inference.normalizer import InferenceStreamNormalizer


class TestInferenceStreamNormalizer:
    def test_to_final_maps_fields(self):
        normalizer = InferenceStreamNormalizer()
        result = InferenceResult(
            content="hello",
            provider="ollama",
            model="qwen2.5:3b",
            status="success",
            ttft_ms=10.0,
            gen_ms=20.0,
            stop_reason="stop",
            metadata={"k": "v"},
        )

        final = normalizer.to_final(result)
        assert final.status == "success"
        assert final.content == "hello"
        assert final.provider == "ollama"
        assert final.metadata["k"] == "v"

    def test_to_stream_events_success_has_deltas_and_final(self):
        normalizer = InferenceStreamNormalizer()
        result = InferenceResult(
            content="abcdef",
            provider="ollama",
            model="m",
            status="success",
            stop_reason="stop",
        )

        events = normalizer.to_stream_events(result, run_id="r1", chunk_size=2)
        assert events[0].event == "delta"
        assert events[1].event == "delta"
        assert events[2].event == "delta"
        assert events[-1].event == "final"
        assert events[-1].data is not None
        assert events[-1].data.content == "abcdef"

    def test_to_stream_events_failed_has_error_and_final(self):
        normalizer = InferenceStreamNormalizer()
        result = InferenceResult(
            content="",
            provider="openvino",
            model="m",
            status="failed",
            error="boom",
            stop_reason="error",
        )

        events = normalizer.to_stream_events(result, run_id="r2")
        assert len(events) == 2
        assert events[0].event == "error"
        assert events[0].error == "boom"
        assert events[1].event == "final"
        assert events[1].data is not None
        assert events[1].data.status == "failed"
