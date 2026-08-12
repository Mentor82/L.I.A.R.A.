"""Unit tests for InferenceGateway."""

import asyncio

import pytest

from services.contracts import InferenceRequest, InferenceResult
from services.inference.gateway import InferenceGateway


@pytest.mark.asyncio
class TestInferenceGateway:
    class _FakeProvider:
        def __init__(self, responses):
            self.responses = list(responses)
            self.calls = 0

        async def infer(self, _request):
            self.calls += 1
            if self.responses:
                return self.responses.pop(0)
            return InferenceResult(content="ok", provider="fake", model="m", status="success")

    async def test_gateway_registers_provider_adapters(self):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "hybrid"})
        assert "ollama" in gateway.providers
        assert "ollama_gpu" in gateway.providers
        assert "ollama_cpu" in gateway.providers
        assert "openvino" in gateway.providers

    async def test_provider_dispatch_ollama(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "ollama"})

        async def fake_ollama(_request):
            return InferenceResult(content="ok", provider="ollama", model="m")

        monkeypatch.setattr(gateway, "_infer_ollama", fake_ollama)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="ollama"))
        assert result.provider == "ollama"
        assert result.content == "ok"

    async def test_provider_dispatch_ollama_gpu(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "ollama_gpu"})

        async def fake_ollama_gpu(_request):
            return InferenceResult(content="gpu-ok", provider="ollama_gpu", model="m-gpu")

        monkeypatch.setattr(gateway, "_infer_ollama_gpu", fake_ollama_gpu)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="ollama_gpu"))
        assert result.provider == "ollama_gpu"
        assert result.content == "gpu-ok"

    async def test_provider_dispatch_ollama_cpu(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "ollama_cpu"})

        async def fake_ollama_cpu(_request):
            return InferenceResult(content="cpu-ok", provider="ollama_cpu", model="m-cpu")

        monkeypatch.setattr(gateway, "_infer_ollama_cpu", fake_ollama_cpu)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="ollama_cpu"))
        assert result.provider == "ollama_cpu"
        assert result.content == "cpu-ok"

    async def test_provider_dispatch_openvino(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "openvino"})

        async def fake_openvino(_request):
            return InferenceResult(content="ok2", provider="openvino", model="m2")

        monkeypatch.setattr(gateway, "_infer_openvino", fake_openvino)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="openvino"))
        assert result.provider == "openvino"
        assert result.content == "ok2"

    async def test_provider_dispatch_openvino_npu_helper(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "openvino_npu_helper"})

        async def fake_helper(_request):
            return InferenceResult(content='{"task_id":"x"}', provider="openvino_npu_helper", model="ov-npu")

        monkeypatch.setattr(gateway, "_infer_openvino_npu_helper", fake_helper)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="openvino_npu_helper"))
        assert result.provider == "openvino_npu_helper"
        assert result.content == '{"task_id":"x"}'

    async def test_hybrid_prefers_first_completed(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "hybrid"})

        async def slow_ollama(_request):
            await asyncio.sleep(0.05)
            return InferenceResult(content="slow", provider="ollama", model="m")

        async def fast_openvino(_request):
            await asyncio.sleep(0.01)
            return InferenceResult(content="fast", provider="openvino", model="m2")

        monkeypatch.setattr(gateway, "_infer_ollama", slow_ollama)
        monkeypatch.setattr(gateway, "_infer_openvino", fast_openvino)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="hybrid"))
        assert result.provider == "hybrid"
        assert result.winner_provider == "openvino"
        assert result.content == "fast"

    async def test_hybrid_handles_error_result(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "hybrid"})

        async def fast_error(_request):
            await asyncio.sleep(0.01)
            return InferenceResult(content="", provider="openvino", model="m", status="failed", error="nope", stop_reason="error")

        async def slow_error(_request):
            await asyncio.sleep(0.02)
            return InferenceResult(content="", provider="ollama", model="m", status="failed", error="nope", stop_reason="error")

        monkeypatch.setattr(gateway, "_infer_openvino", fast_error)
        monkeypatch.setattr(gateway, "_infer_ollama", slow_error)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="hybrid"))
        assert result.provider == "hybrid"
        assert result.status == "failed"
        assert result.stop_reason == "error"

    async def test_hybrid_prefers_late_success_over_early_error(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "hybrid"})

        async def early_error(_request):
            await asyncio.sleep(0.01)
            return InferenceResult(content="", provider="openvino", model="m", status="failed", error="nope", stop_reason="error")

        async def late_success(_request):
            await asyncio.sleep(0.03)
            return InferenceResult(content="actual answer", provider="ollama", model="m")

        monkeypatch.setattr(gateway, "_infer_openvino", early_error)
        monkeypatch.setattr(gateway, "_infer_ollama", late_success)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="hybrid"))
        assert result.provider == "hybrid"
        assert result.winner_provider == "ollama"
        assert result.content == "actual answer"
        assert "openvino" in result.metadata.get("failed_providers", [])

    async def test_openvino_missing_model_dir(self):
        gateway = InferenceGateway(config={"OPENVINO_GENAI_MODEL_DIR": ""})
        result = await gateway._infer_openvino(InferenceRequest(prompt="x", provider="openvino"))
        assert result.provider == "openvino"
        assert result.status == "failed"
        assert result.error is not None
        assert result.content == ""

    async def test_ollama_telemetry_converted_from_nanoseconds(self, monkeypatch):
        """Ollama ns durations must be converted to ms; verify values are < raw ns."""
        gateway = InferenceGateway(config={})

        class FakeResp:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield (
                    '{"thinking":"","response":"hello","done":true,'
                    '"total_duration":2000000000,'
                    '"prompt_eval_duration":500000000,'
                    '"load_duration":100000000}'
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            def stream(self, *a, **kw): return FakeResp()

        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: FakeClient())

        result = await gateway._infer_ollama(InferenceRequest(prompt="hi", provider="ollama"))
        assert result.gen_ms == pytest.approx(2000.0)
        assert result.ttft_ms == pytest.approx(500.0)
        assert result.load_ms == pytest.approx(100.0)

    async def test_hybrid_cancellation_metadata_populated(self, monkeypatch):
        """Cancelled provider name appears in result metadata."""
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "hybrid"})

        async def fast_ollama(_r):
            return InferenceResult(content="fast", provider="ollama", model="m")

        async def slow_openvino(_r):
            import asyncio as _a
            await _a.sleep(10)
            return InferenceResult(content="slow", provider="openvino", model="m2")

        monkeypatch.setattr(gateway, "_infer_ollama", fast_ollama)
        monkeypatch.setattr(gateway, "_infer_openvino", slow_openvino)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="hybrid"))
        assert result.winner_provider == "ollama"
        assert "openvino" in result.metadata.get("cancelled_providers", [])

    async def test_hybrid_winner_telemetry_propagated(self, monkeypatch):
        """ttft_ms/gen_ms/load_ms from the winning provider are preserved."""
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "hybrid"})

        async def fast_openvino(_r):
            return InferenceResult(
                content="ov result", provider="openvino", model="ov",
                ttft_ms=42.0, gen_ms=99.0, load_ms=10.0,
            )

        async def slow_ollama(_r):
            import asyncio as _a
            await _a.sleep(10)
            return InferenceResult(content="slow", provider="ollama", model="m")

        monkeypatch.setattr(gateway, "_infer_openvino", fast_openvino)
        monkeypatch.setattr(gateway, "_infer_ollama", slow_ollama)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="hybrid"))
        assert result.ttft_ms == pytest.approx(42.0)
        assert result.gen_ms == pytest.approx(99.0)
        assert result.load_ms == pytest.approx(10.0)

    async def test_gateway_normalize_result(self):
        gateway = InferenceGateway(config={})
        normalized = gateway.normalize_result(
            InferenceResult(
                content="ok",
                provider="ollama",
                model="m",
                status="success",
            )
        )
        assert normalized.status == "success"
        assert normalized.content == "ok"

    async def test_gateway_to_stream_events(self):
        gateway = InferenceGateway(config={})
        events = gateway.to_stream_events(
            InferenceResult(
                content="abcd",
                provider="ollama",
                model="m",
                status="success",
            ),
            run_id="run-1",
            chunk_size=2,
        )
        assert events[0].event == "delta"
        assert events[-1].event == "final"

    async def test_ollama_error_sets_status_and_error_field(self, monkeypatch):
        """Ollama network failure must set status=failed and populate error, not content."""
        gateway = InferenceGateway(config={})

        class FakeClient:
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            def stream(self, *a, **kw):
                raise ConnectionError("connection refused")

        monkeypatch.setattr("httpx.AsyncClient", lambda **kw: FakeClient())

        result = await gateway._infer_ollama(InferenceRequest(prompt="hi", provider="ollama"))
        assert result.status == "failed"
        assert result.error is not None
        assert "connection refused" in result.error
        assert result.content == ""

    async def test_hybrid_all_fail_returns_status_failed(self, monkeypatch):
        """If all providers fail, hybrid result must have status=failed and non-empty error."""
        gateway = InferenceGateway(config={})

        async def failing(_r):
            return InferenceResult(
                content="", provider="ollama", model="m",
                status="failed", error="boom", stop_reason="error",
            )

        monkeypatch.setattr(gateway, "_infer_ollama", failing)
        monkeypatch.setattr(gateway, "_infer_openvino", failing)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="hybrid"))
        assert result.status == "failed"
        assert result.error is not None
        assert result.content == ""

    async def test_ll_ol_fallback_prefers_llama_cpp(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "ll_ol_fallback"})

        async def fake_ll(_request):
            return InferenceResult(content="ll-ok", provider="llama_cpp", model="mll")

        async def fake_ol(_request):
            return InferenceResult(content="ol-ok", provider="ollama", model="mol")

        monkeypatch.setattr(gateway, "_infer_llama_cpp", fake_ll)
        monkeypatch.setattr(gateway, "_infer_ollama", fake_ol)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="ll_ol_fallback"))
        assert result.provider == "ll_ol_fallback"
        assert result.winner_provider == "llama_cpp"
        assert result.content == "ll-ok"

    async def test_ll_ol_fallback_uses_ollama_on_llama_cpp_failure(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "ll_ol_fallback"})

        async def fail_ll(_request):
            return InferenceResult(
                content="",
                provider="llama_cpp",
                model="mll",
                status="failed",
                error="ll-down",
                stop_reason="error",
            )

        async def ok_ol_gpu(_request):
            return InferenceResult(content="ol-gpu-ok", provider="ollama_gpu", model="mol-gpu")

        async def ok_ol_cpu(_request):
            return InferenceResult(content="ol-cpu-ok", provider="ollama_cpu", model="mol-cpu")

        monkeypatch.setattr(gateway, "_infer_llama_cpp", fail_ll)
        monkeypatch.setattr(gateway, "_infer_ollama_gpu", ok_ol_gpu)
        monkeypatch.setattr(gateway, "_infer_ollama_cpu", ok_ol_cpu)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="ll_ol_fallback"))
        assert result.provider == "ll_ol_fallback"
        assert result.winner_provider == "ollama_gpu"
        assert result.content == "ol-gpu-ok"
        assert result.metadata.get("primary_error") == "ll-down"

    async def test_ll_ol_fallback_uses_ollama_cpu_when_gpu_fails(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "ll_ol_fallback"})

        async def fail_ll(_request):
            return InferenceResult(
                content="",
                provider="llama_cpp",
                model="mll",
                status="failed",
                error="ll-down",
                stop_reason="error",
            )

        async def fail_ol_gpu(_request):
            return InferenceResult(
                content="",
                provider="ollama_gpu",
                model="mol-gpu",
                status="failed",
                error="ol-gpu-down",
                stop_reason="error",
            )

        async def ok_ol_cpu(_request):
            return InferenceResult(content="ol-cpu-ok", provider="ollama_cpu", model="mol-cpu")

        monkeypatch.setattr(gateway, "_infer_llama_cpp", fail_ll)
        monkeypatch.setattr(gateway, "_infer_ollama_gpu", fail_ol_gpu)
        monkeypatch.setattr(gateway, "_infer_ollama_cpu", ok_ol_cpu)

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="ll_ol_fallback"))
        assert result.provider == "ll_ol_fallback"
        assert result.winner_provider == "ollama_cpu"
        assert result.content == "ol-cpu-ok"
        assert result.metadata.get("secondary_fallback_error") == "ol-gpu-down"

    async def test_provider_aliases_ll_and_ol(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "ll_ol_fallback"})

        async def fake_ll(_request):
            return InferenceResult(content="ll", provider="llama_cpp", model="mll")

        async def fake_ol(_request):
            return InferenceResult(content="ol", provider="ollama", model="mol")

        monkeypatch.setattr(gateway, "_infer_llama_cpp", fake_ll)
        monkeypatch.setattr(gateway, "_infer_ollama", fake_ol)

        result_ll = await gateway.infer(InferenceRequest(prompt="hi", provider="ll"))
        result_ol = await gateway.infer(InferenceRequest(prompt="hi", provider="ol"))
        assert result_ll.provider == "llama_cpp"
        assert result_ll.content == "ll"
        assert result_ol.provider == "ollama"
        assert result_ol.content == "ol"

    async def test_provider_aliases_ol_gpu_and_ol_cpu(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "ll_ol_fallback"})

        async def fake_ol_gpu(_request):
            return InferenceResult(content="ol-gpu", provider="ollama_gpu", model="mol-gpu")

        async def fake_ol_cpu(_request):
            return InferenceResult(content="ol-cpu", provider="ollama_cpu", model="mol-cpu")

        monkeypatch.setattr(gateway, "_infer_ollama_gpu", fake_ol_gpu)
        monkeypatch.setattr(gateway, "_infer_ollama_cpu", fake_ol_cpu)

        result_gpu = await gateway.infer(InferenceRequest(prompt="hi", provider="ol_gpu"))
        result_cpu = await gateway.infer(InferenceRequest(prompt="hi", provider="ol_cpu"))
        assert result_gpu.provider == "ollama_gpu"
        assert result_gpu.content == "ol-gpu"
        assert result_cpu.provider == "ollama_cpu"
        assert result_cpu.content == "ol-cpu"

    async def test_provider_alias_openvino_helper(self, monkeypatch):
        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "ll_ol_fallback"})

        async def fake_helper(_request):
            return InferenceResult(content='{"task_id":"alias"}', provider="openvino_npu_helper", model="ov-npu")

        monkeypatch.setattr(gateway, "_infer_openvino_npu_helper", fake_helper)

        result_alias1 = await gateway.infer(InferenceRequest(prompt="hi", provider="ov_npu_helper"))
        result_alias2 = await gateway.infer(InferenceRequest(prompt="hi", provider="openvino_helper"))
        assert result_alias1.provider == "openvino_npu_helper"
        assert result_alias2.provider == "openvino_npu_helper"

    async def test_llama_cpp_auto_annotates_build_variant(self, monkeypatch):
        """llama_cpp_auto provider embeds build_variant in result metadata."""
        import pathlib
        from services.inference.llama_cpp_server import LlamaCppServerManager

        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "llama_cpp_auto", "LLAMA_CPP_BUILD_VARIANT": "auto"})

        async def fake_ll(_request):
            return InferenceResult(content="ok", provider="llama_cpp", model="m", metadata={})

        monkeypatch.setattr(gateway, "_infer_llama_cpp", fake_ll)
        monkeypatch.setattr(
            LlamaCppServerManager,
            "find_available_build",
            classmethod(lambda cls, preferred_variant="auto": ("cpu-avx2-f16c", pathlib.Path("cpu-avx2-f16c/llama-server.exe"))),
        )

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="llama_cpp_auto"))
        assert result.provider == "llama_cpp_auto"
        assert result.metadata.get("build_variant") == "cpu-avx2-f16c"

    async def test_llama_cpp_auto_alias_ll_auto(self, monkeypatch):
        """'ll_auto' is a valid alias for 'llama_cpp_auto'."""
        import pathlib
        from services.inference.llama_cpp_server import LlamaCppServerManager

        gateway = InferenceGateway(config={"DEFAULT_LLM_PROVIDER": "llama_cpp_auto", "LLAMA_CPP_BUILD_VARIANT": "auto"})

        async def fake_ll(_request):
            return InferenceResult(content="ok", provider="llama_cpp", model="m", metadata={})

        monkeypatch.setattr(gateway, "_infer_llama_cpp", fake_ll)
        monkeypatch.setattr(
            LlamaCppServerManager,
            "find_available_build",
            classmethod(lambda cls, preferred_variant="auto": ("vulkan-cross-gpu", pathlib.Path("vulkan-cross-gpu/llama-server.exe"))),
        )

        result = await gateway.infer(InferenceRequest(prompt="hi", provider="ll_auto"))
        assert result.provider == "llama_cpp_auto"
        assert result.metadata.get("build_variant") == "vulkan-cross-gpu"

    async def test_breaker_opens_after_threshold_and_blocks_calls(self, monkeypatch):
        gateway = InferenceGateway(
            config={
                "INFERENCE_BREAKER_ENABLED": True,
                "INFERENCE_BREAKER_FAILURE_THRESHOLD": 2,
                "INFERENCE_BREAKER_COOLDOWN_SECONDS": 30,
            }
        )
        fake = self._FakeProvider(
            [
                InferenceResult(content="", provider="llama_cpp", model="m", status="failed", error="e1", stop_reason="error"),
                InferenceResult(content="", provider="llama_cpp", model="m", status="failed", error="e2", stop_reason="error"),
            ]
        )
        gateway.providers["llama_cpp"] = fake
        monkeypatch.setattr(gateway, "_now", lambda: 1000.0)

        r1 = await gateway._infer_llama_cpp(InferenceRequest(prompt="a", provider="llama_cpp"))
        r2 = await gateway._infer_llama_cpp(InferenceRequest(prompt="b", provider="llama_cpp"))
        r3 = await gateway._infer_llama_cpp(InferenceRequest(prompt="c", provider="llama_cpp"))

        assert r1.status == "failed"
        assert r2.status == "failed"
        assert r3.status == "failed"
        assert "circuit breaker open" in (r3.error or "")
        assert fake.calls == 2

    async def test_breaker_half_open_probe_closes_on_success(self, monkeypatch):
        gateway = InferenceGateway(
            config={
                "INFERENCE_BREAKER_ENABLED": True,
                "INFERENCE_BREAKER_FAILURE_THRESHOLD": 1,
                "INFERENCE_BREAKER_COOLDOWN_SECONDS": 10,
            }
        )
        fake = self._FakeProvider(
            [
                InferenceResult(content="", provider="llama_cpp", model="m", status="failed", error="e1", stop_reason="error"),
                InferenceResult(content="probe-ok", provider="llama_cpp", model="m", status="success"),
                InferenceResult(content="next-ok", provider="llama_cpp", model="m", status="success"),
            ]
        )
        gateway.providers["llama_cpp"] = fake

        now = {"t": 0.0}
        monkeypatch.setattr(gateway, "_now", lambda: now["t"])

        first = await gateway._infer_llama_cpp(InferenceRequest(prompt="a", provider="llama_cpp"))
        now["t"] = 5.0
        blocked = await gateway._infer_llama_cpp(InferenceRequest(prompt="b", provider="llama_cpp"))
        now["t"] = 11.0
        probe = await gateway._infer_llama_cpp(InferenceRequest(prompt="c", provider="llama_cpp"))
        now["t"] = 12.0
        after_close = await gateway._infer_llama_cpp(InferenceRequest(prompt="d", provider="llama_cpp"))

        assert first.status == "failed"
        assert blocked.status == "failed"
        assert "circuit breaker open" in (blocked.error or "")
        assert probe.status == "success"
        assert after_close.status == "success"
        assert fake.calls == 3
