"""
Live E2E integration tests for InferenceGateway with real Ollama.

Skipped by default. Set the following env var to opt in:

    RUN_LIVE_INFERENCE_TESTS=1

Ollama must be reachable at OLLAMA_HOST:OLLAMA_PORT (defaults: 127.0.0.1:11434)
with OLLAMA_MODEL available (default: qwen2.5:3b).

Invoke from any working directory:

    c:/ai/LIARA/.venv/Scripts/python.exe -m pytest c:/ai/LIARA/tests/integration/test_inference_live.py -v
"""

import os

import pytest

from services.contracts import InferenceRequest
from services.inference.gateway import InferenceGateway

# ---------------------------------------------------------------------------
# Skip guard
# ---------------------------------------------------------------------------

_SKIP = not bool(os.environ.get("RUN_LIVE_INFERENCE_TESTS"))
_SKIP_REASON = "RUN_LIVE_INFERENCE_TESTS not set"

skip_live = pytest.mark.skipif(_SKIP, reason=_SKIP_REASON)

# Gateway config from env or .env defaults.
_CONFIG = {
    "OLLAMA_HOST": os.environ.get("OLLAMA_HOST", "127.0.0.1"),
    "OLLAMA_PORT": os.environ.get("OLLAMA_PORT", "11434"),
    "OLLAMA_MODEL": os.environ.get("OLLAMA_MODEL", "qwen2.5:3b"),
}


# ---------------------------------------------------------------------------
# Ollama E2E tests
# ---------------------------------------------------------------------------


@skip_live
@pytest.mark.asyncio
class TestOllamaLive:
    """Real Ollama response parsing and telemetry verification."""

    async def test_basic_response_is_non_empty(self):
        gateway = InferenceGateway(config=_CONFIG)
        result = await gateway.infer(
            InferenceRequest(prompt="Reply with exactly one word: hello", max_tokens=8, provider="ollama")
        )
        assert result.status == "success", f"Expected success, got: {result.status!r} error={result.error!r}"
        assert result.provider == "ollama"
        assert len(result.content.strip()) > 0

    async def test_response_content_is_string(self):
        gateway = InferenceGateway(config=_CONFIG)
        result = await gateway.infer(
            InferenceRequest(prompt="What is 1+1?", max_tokens=16, provider="ollama")
        )
        assert result.status == "success"
        assert isinstance(result.content, str)

    async def test_telemetry_gen_ms_is_positive(self):
        """gen_ms must be a positive number (converted from Ollama nanosecond total_duration)."""
        gateway = InferenceGateway(config=_CONFIG)
        result = await gateway.infer(
            InferenceRequest(prompt="Say: ok", max_tokens=8, provider="ollama")
        )
        assert result.status == "success"
        assert result.gen_ms is not None
        assert result.gen_ms > 0, f"gen_ms={result.gen_ms} should be positive"

    async def test_telemetry_ttft_ms_is_positive(self):
        """ttft_ms (prompt_eval_duration converted from ns) must be positive."""
        gateway = InferenceGateway(config=_CONFIG)
        result = await gateway.infer(
            InferenceRequest(prompt="Say: ok", max_tokens=8, provider="ollama")
        )
        assert result.status == "success"
        assert result.ttft_ms is not None
        assert result.ttft_ms > 0, f"ttft_ms={result.ttft_ms} should be positive"

    async def test_telemetry_values_are_ms_not_ns(self):
        """Nanosecond values would be > 1e8; confirm conversion produces ms-range values."""
        gateway = InferenceGateway(config=_CONFIG)
        result = await gateway.infer(
            InferenceRequest(prompt="Say: ok", max_tokens=8, provider="ollama")
        )
        assert result.status == "success"
        # A real model won't finish in under 1 µs (< 0.001 ms) and won't take > 5 min (300 000 ms)
        assert 0.001 < result.gen_ms < 300_000, f"gen_ms={result.gen_ms} looks like raw ns, not ms"

    async def test_model_field_is_populated(self):
        gateway = InferenceGateway(config=_CONFIG)
        result = await gateway.infer(
            InferenceRequest(prompt="hi", max_tokens=8, provider="ollama")
        )
        assert result.status == "success"
        assert result.model  # non-empty string

    async def test_stop_reason_is_not_error(self):
        gateway = InferenceGateway(config=_CONFIG)
        result = await gateway.infer(
            InferenceRequest(prompt="hi", max_tokens=16, provider="ollama")
        )
        assert result.status == "success"
        assert result.stop_reason != "error"

    async def test_error_field_is_none_on_success(self):
        gateway = InferenceGateway(config=_CONFIG)
        result = await gateway.infer(
            InferenceRequest(prompt="hi", max_tokens=8, provider="ollama")
        )
        assert result.status == "success"
        assert result.error is None


# ---------------------------------------------------------------------------
# Hybrid E2E: Ollama wins (OpenVINO not configured → fails fast, Ollama succeeds)
# ---------------------------------------------------------------------------


@skip_live
@pytest.mark.asyncio
class TestHybridOllamaLive:
    """Hybrid mode with real Ollama as the surviving provider."""

    async def test_hybrid_returns_success_when_ollama_available(self):
        """
        OpenVINO is not configured (no model dir) → fails immediately.
        Ollama is live → hybrid result must be success from Ollama.
        """
        config = {**_CONFIG, "OPENVINO_GENAI_MODEL_DIR": ""}
        gateway = InferenceGateway(config=config)
        result = await gateway.infer(
            InferenceRequest(prompt="Say: ok", max_tokens=16, provider="hybrid")
        )
        assert result.status == "success", f"Expected success, got: {result.status!r} error={result.error!r}"
        assert result.provider == "hybrid"
        assert result.winner_provider == "ollama"
        assert len(result.content.strip()) > 0

    async def test_hybrid_winner_telemetry_present(self):
        config = {**_CONFIG, "OPENVINO_GENAI_MODEL_DIR": ""}
        gateway = InferenceGateway(config=config)
        result = await gateway.infer(
            InferenceRequest(prompt="Say: ok", max_tokens=16, provider="hybrid")
        )
        assert result.status == "success"
        assert result.gen_ms is not None and result.gen_ms > 0

    async def test_hybrid_failed_providers_lists_openvino(self):
        """OpenVINO fails fast → appears in failed_providers metadata."""
        config = {**_CONFIG, "OPENVINO_GENAI_MODEL_DIR": ""}
        gateway = InferenceGateway(config=config)
        result = await gateway.infer(
            InferenceRequest(prompt="Say: ok", max_tokens=16, provider="hybrid")
        )
        assert result.status == "success"
        assert "openvino" in result.metadata.get("failed_providers", [])
