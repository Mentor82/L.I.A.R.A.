"""
Inference gateway - routes to local or remote LLM providers.

Supports:
- Ollama (local)
- OpenVINO (local NPU/CPU)
- Hybrid mode (races both)
"""

import asyncio
import time
from typing import Any, Dict

from services.config import Settings
from services.contracts import (
    InferenceNormalizedResponse,
    InferenceRequest,
    InferenceResult,
    InferenceStreamEvent,
)

from .normalizer import InferenceStreamNormalizer
from .providers import LlamaCppProvider, OllamaProvider, OpenVINOProvider, OpenVINONpuHelperProvider
from .llama_cpp_server import LlamaCppServerManager


class InferenceGateway:
    """Routes inference requests to appropriate backend."""

    def __init__(self, config: Dict[str, Any] | None = None):
        self.config = config or Settings.to_dict()
        self.default_provider = self.config.get("DEFAULT_LLM_PROVIDER", "ll_ol_fallback")

        self.llama_cpp_base_url = self.config.get("LLAMA_CPP_BASE_URL", "http://127.0.0.1:8000")
        self.llama_cpp_model = self.config.get("LLAMA_CPP_MODEL", "qwen2.5-3b-ollama-export.gguf")
        self.llama_cpp_timeout_seconds = float(self.config.get("LLAMA_CPP_TIMEOUT_SECONDS", 120))

        self.ollama_host = self.config.get("OLLAMA_HOST", "127.0.0.1")
        self.ollama_port = int(self.config.get("OLLAMA_PORT", 11434))
        self.ollama_model = self.config.get("OLLAMA_MODEL", "qwen2.5:3b")
        self.ollama_timeout_seconds = float(self.config.get("OLLAMA_TIMEOUT_SECONDS", self.llama_cpp_timeout_seconds))
        self.ollama_gpu_host = self.config.get("OLLAMA_GPU_HOST", self.ollama_host)
        self.ollama_gpu_port = int(self.config.get("OLLAMA_GPU_PORT", self.ollama_port))
        self.ollama_gpu_model = self.config.get("OLLAMA_GPU_MODEL", self.ollama_model)
        self.ollama_gpu_timeout_seconds = float(
            self.config.get("OLLAMA_GPU_TIMEOUT_SECONDS", self.ollama_timeout_seconds)
        )
        self.ollama_cpu_host = self.config.get("OLLAMA_CPU_HOST", self.ollama_host)
        self.ollama_cpu_port = int(self.config.get("OLLAMA_CPU_PORT", self.ollama_port))
        self.ollama_cpu_model = self.config.get("OLLAMA_CPU_MODEL", self.ollama_model)
        self.ollama_cpu_timeout_seconds = float(
            self.config.get("OLLAMA_CPU_TIMEOUT_SECONDS", self.ollama_timeout_seconds)
        )
        self.openvino_npu_base_url = self.config.get("OPENVINO_NPU_BASE_URL", "http://127.0.0.1:8040")
        self.openvino_npu_helper_timeout_seconds = float(
            self.config.get("OPENVINO_NPU_HELPER_TIMEOUT_SECONDS", 120)
        )
        self.openvino_npu_helper_task_type = self.config.get("OPENVINO_NPU_HELPER_TASK_TYPE", "quick_extract")
        expected_fields_raw = str(
            self.config.get("OPENVINO_NPU_HELPER_EXPECTED_FIELDS", "task_id,key_points,confidence")
        )
        self.openvino_npu_helper_expected_fields = [
            field.strip() for field in expected_fields_raw.split(",") if field.strip()
        ] or ["task_id", "key_points", "confidence"]
        self.breaker_enabled = bool(self.config.get("INFERENCE_BREAKER_ENABLED", True))
        self.breaker_failure_threshold = int(self.config.get("INFERENCE_BREAKER_FAILURE_THRESHOLD", 3))
        self.breaker_cooldown_seconds = float(self.config.get("INFERENCE_BREAKER_COOLDOWN_SECONDS", 90))
        self._breaker_state: Dict[str, Dict[str, Any]] = {}
        self.providers = {
            "llama_cpp": LlamaCppProvider(
                base_url=self.llama_cpp_base_url,
                model=self.llama_cpp_model,
                timeout_seconds=self.llama_cpp_timeout_seconds,
            ),
            "ollama": OllamaProvider(
                host=self.ollama_host,
                port=self.ollama_port,
                model=self.ollama_model,
                timeout_seconds=self.ollama_timeout_seconds,
            ),
            "ollama_gpu": OllamaProvider(
                host=self.ollama_gpu_host,
                port=self.ollama_gpu_port,
                model=self.ollama_gpu_model,
                timeout_seconds=self.ollama_gpu_timeout_seconds,
            ),
            "ollama_cpu": OllamaProvider(
                host=self.ollama_cpu_host,
                port=self.ollama_cpu_port,
                model=self.ollama_cpu_model,
                timeout_seconds=self.ollama_cpu_timeout_seconds,
            ),
            "openvino": OpenVINOProvider(
                model_dir=self.config.get("OPENVINO_GENAI_MODEL_DIR") or self.config.get("OPENVINO_MODEL_DIR"),
                device=self.config.get("OPENVINO_GENAI_DEVICE") or self.config.get("OPENVINO_DEVICE", "CPU"),
            ),
            "openvino_npu_helper": OpenVINONpuHelperProvider(
                base_url=self.openvino_npu_base_url,
                timeout_seconds=self.openvino_npu_helper_timeout_seconds,
                default_task_type=self.openvino_npu_helper_task_type,
                expected_fields=self.openvino_npu_helper_expected_fields,
            ),
        }
        self.normalizer = InferenceStreamNormalizer()

    @staticmethod
    def _now() -> float:
        return time.time()

    def _state_for(self, provider_name: str) -> Dict[str, Any]:
        state = self._breaker_state.get(provider_name)
        if state is None:
            state = {
                "consecutive_failures": 0,
                "opened_until": 0.0,
                "half_open_probe_in_flight": False,
            }
            self._breaker_state[provider_name] = state
        return state

    def _breaker_snapshot(self, provider_name: str) -> Dict[str, Any]:
        state = self._state_for(provider_name)
        now = self._now()
        opened_until = float(state.get("opened_until", 0.0) or 0.0)
        is_open = opened_until > now
        breaker_state = "open" if is_open else ("half_open" if opened_until > 0 else "closed")
        return {
            "state": breaker_state,
            "consecutive_failures": int(state.get("consecutive_failures", 0) or 0),
            "opened_until": opened_until,
            "cooldown_remaining_seconds": max(0.0, opened_until - now),
            "half_open_probe_in_flight": bool(state.get("half_open_probe_in_flight", False)),
        }

    def _record_success(self, provider_name: str) -> None:
        state = self._state_for(provider_name)
        state["consecutive_failures"] = 0
        state["opened_until"] = 0.0
        state["half_open_probe_in_flight"] = False

    def _record_failure(self, provider_name: str, *, was_half_open_probe: bool) -> None:
        state = self._state_for(provider_name)
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0) or 0) + 1
        should_open = was_half_open_probe or state["consecutive_failures"] >= self.breaker_failure_threshold
        if should_open:
            state["opened_until"] = self._now() + self.breaker_cooldown_seconds
            state["half_open_probe_in_flight"] = False

    async def _call_with_breaker(
        self,
        *,
        provider_name: str,
        request: InferenceRequest,
        exposed_provider: str,
    ) -> InferenceResult:
        if not self.breaker_enabled:
            return await self.providers[provider_name].infer(request)

        state = self._state_for(provider_name)
        now = self._now()
        opened_until = float(state.get("opened_until", 0.0) or 0.0)

        if opened_until > now:
            snap = self._breaker_snapshot(provider_name)
            return InferenceResult(
                content="",
                provider=exposed_provider,
                model=request.model or "",
                status="failed",
                error=f"circuit breaker open for provider={provider_name}",
                stop_reason="error",
                metadata={"breaker": {"provider": provider_name, **snap}},
            )

        was_half_open_probe = opened_until > 0 and opened_until <= now
        if was_half_open_probe:
            if bool(state.get("half_open_probe_in_flight", False)):
                snap = self._breaker_snapshot(provider_name)
                return InferenceResult(
                    content="",
                    provider=exposed_provider,
                    model=request.model or "",
                    status="failed",
                    error=f"circuit breaker half-open probe already in flight for provider={provider_name}",
                    stop_reason="error",
                    metadata={"breaker": {"provider": provider_name, **snap}},
                )
            state["half_open_probe_in_flight"] = True

        try:
            result = await self.providers[provider_name].infer(request)
        except Exception as exc:
            self._record_failure(provider_name, was_half_open_probe=was_half_open_probe)
            snap = self._breaker_snapshot(provider_name)
            return InferenceResult(
                content="",
                provider=exposed_provider,
                model=request.model or "",
                status="failed",
                error=str(exc),
                stop_reason="error",
                metadata={"breaker": {"provider": provider_name, **snap}},
            )

        if result.status == "success":
            self._record_success(provider_name)
        else:
            self._record_failure(provider_name, was_half_open_probe=was_half_open_probe)

        snap = self._breaker_snapshot(provider_name)
        meta = dict(result.metadata or {})
        meta["breaker"] = {"provider": provider_name, **snap}
        return InferenceResult(
            content=result.content,
            provider=result.provider,
            model=result.model,
            status=result.status,
            error=result.error,
            ttft_ms=result.ttft_ms,
            gen_ms=result.gen_ms,
            load_ms=result.load_ms,
            stop_reason=result.stop_reason,
            winner_provider=result.winner_provider,
            metadata=meta,
        )

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        provider = self._normalize_provider(request.provider or self.default_provider)

        if provider == "llama_cpp":
            return await self._infer_llama_cpp(request)
        if provider == "llama_cpp_auto":
            return await self._infer_llama_cpp_auto(request)
        if provider == "ollama":
            return await self._infer_ollama(request)
        if provider == "ollama_gpu":
            return await self._infer_ollama_gpu(request)
        if provider == "ollama_cpu":
            return await self._infer_ollama_cpu(request)
        if provider == "openvino":
            return await self._infer_openvino(request)
        if provider == "openvino_npu_helper":
            return await self._infer_openvino_npu_helper(request)
        if provider == "ll_ol_fallback":
            return await self._infer_ll_ol_fallback(request)
        if provider == "hybrid":
            return await self._infer_hybrid(request)
        raise ValueError(f"Unknown provider: {provider}")

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        aliases = {
            "ll": "llama_cpp",
            "llama.cpp": "llama_cpp",
            "ol": "ollama",
            "ol_gpu": "ollama_gpu",
            "ol_cpu": "ollama_cpu",
            "ll_ol": "ll_ol_fallback",
            "ll_ol_fallback": "ll_ol_fallback",
            "llama_cpp_auto": "llama_cpp_auto",
            "ll_auto": "llama_cpp_auto",
            "ov_npu_helper": "openvino_npu_helper",
            "openvino_helper": "openvino_npu_helper",
        }
        return aliases.get(provider, provider)

    async def _infer_llama_cpp(self, request: InferenceRequest) -> InferenceResult:
        return await self._call_with_breaker(
            provider_name="llama_cpp",
            request=request,
            exposed_provider="llama_cpp",
        )

    async def _infer_llama_cpp_auto(self, request: InferenceRequest) -> InferenceResult:
        """Like llama_cpp but annotates result with the active build variant."""
        result = await self._infer_llama_cpp(request)
        try:
            preferred = self.config.get("LLAMA_CPP_BUILD_VARIANT", "auto")
            variant, _ = LlamaCppServerManager.find_available_build(preferred_variant=preferred)
        except FileNotFoundError:
            variant = "unknown"
        meta = dict(result.metadata or {})
        meta["build_variant"] = variant
        return InferenceResult(
            content=result.content,
            provider="llama_cpp_auto",
            model=result.model,
            status=result.status,
            error=result.error,
            ttft_ms=result.ttft_ms,
            gen_ms=result.gen_ms,
            load_ms=result.load_ms,
            stop_reason=result.stop_reason,
            winner_provider=result.winner_provider,
            metadata=meta,
        )

    async def _infer_ollama(self, request: InferenceRequest) -> InferenceResult:
        # Keep legacy provider path mapped to logical GPU tier by default.
        result = await self._infer_ollama_gpu(request)
        meta = dict(result.metadata or {})
        meta.setdefault("logical_backend", "ollama_gpu")
        return InferenceResult(
            content=result.content,
            provider="ollama",
            model=result.model,
            status=result.status,
            error=result.error,
            ttft_ms=result.ttft_ms,
            gen_ms=result.gen_ms,
            load_ms=result.load_ms,
            stop_reason=result.stop_reason,
            winner_provider=result.winner_provider,
            metadata=meta,
        )

    async def _infer_ollama_gpu(self, request: InferenceRequest) -> InferenceResult:
        result = await self._call_with_breaker(
            provider_name="ollama_gpu",
            request=request,
            exposed_provider="ollama_gpu",
        )
        meta = dict(result.metadata or {})
        meta["logical_backend"] = "ollama_gpu"
        return InferenceResult(
            content=result.content,
            provider="ollama_gpu",
            model=result.model,
            status=result.status,
            error=result.error,
            ttft_ms=result.ttft_ms,
            gen_ms=result.gen_ms,
            load_ms=result.load_ms,
            stop_reason=result.stop_reason,
            winner_provider=result.winner_provider,
            metadata=meta,
        )

    async def _infer_ollama_cpu(self, request: InferenceRequest) -> InferenceResult:
        result = await self._call_with_breaker(
            provider_name="ollama_cpu",
            request=request,
            exposed_provider="ollama_cpu",
        )
        meta = dict(result.metadata or {})
        meta["logical_backend"] = "ollama_cpu"
        return InferenceResult(
            content=result.content,
            provider="ollama_cpu",
            model=result.model,
            status=result.status,
            error=result.error,
            ttft_ms=result.ttft_ms,
            gen_ms=result.gen_ms,
            load_ms=result.load_ms,
            stop_reason=result.stop_reason,
            winner_provider=result.winner_provider,
            metadata=meta,
        )

    async def _infer_openvino(self, request: InferenceRequest) -> InferenceResult:
        return await self._call_with_breaker(
            provider_name="openvino",
            request=request,
            exposed_provider="openvino",
        )

    async def _infer_openvino_npu_helper(self, request: InferenceRequest) -> InferenceResult:
        return await self._call_with_breaker(
            provider_name="openvino_npu_helper",
            request=request,
            exposed_provider="openvino_npu_helper",
        )

    async def _infer_ll_ol_fallback(self, request: InferenceRequest) -> InferenceResult:
        ll_result = await self._infer_llama_cpp(request)
        if ll_result.status == "success":
            return InferenceResult(
                content=ll_result.content,
                provider="ll_ol_fallback",
                model=ll_result.model,
                ttft_ms=ll_result.ttft_ms,
                gen_ms=ll_result.gen_ms,
                load_ms=ll_result.load_ms,
                winner_provider="llama_cpp",
                stop_reason=ll_result.stop_reason,
                metadata={"primary_provider": "llama_cpp", **(ll_result.metadata or {})},
            )

        ol_gpu_result = await self._infer_ollama_gpu(request)
        if ol_gpu_result.status == "success":
            return InferenceResult(
                content=ol_gpu_result.content,
                provider="ll_ol_fallback",
                model=ol_gpu_result.model,
                ttft_ms=ol_gpu_result.ttft_ms,
                gen_ms=ol_gpu_result.gen_ms,
                load_ms=ol_gpu_result.load_ms,
                winner_provider="ollama_gpu",
                stop_reason=ol_gpu_result.stop_reason,
                metadata={
                    "primary_provider": "llama_cpp",
                    "fallback_provider": "ollama_gpu",
                    "primary_error": ll_result.error,
                    "primary_metadata": ll_result.metadata or {},
                    **(ol_gpu_result.metadata or {}),
                },
            )

        ol_cpu_result = await self._infer_ollama_cpu(request)
        if ol_cpu_result.status == "success":
            return InferenceResult(
                content=ol_cpu_result.content,
                provider="ll_ol_fallback",
                model=ol_cpu_result.model,
                ttft_ms=ol_cpu_result.ttft_ms,
                gen_ms=ol_cpu_result.gen_ms,
                load_ms=ol_cpu_result.load_ms,
                winner_provider="ollama_cpu",
                stop_reason=ol_cpu_result.stop_reason,
                metadata={
                    "primary_provider": "llama_cpp",
                    "fallback_provider": "ollama_cpu",
                    "secondary_fallback_provider": "ollama_gpu",
                    "primary_error": ll_result.error,
                    "secondary_fallback_error": ol_gpu_result.error,
                    "primary_metadata": ll_result.metadata or {},
                    "secondary_fallback_metadata": ol_gpu_result.metadata or {},
                    **(ol_cpu_result.metadata or {}),
                },
            )

        return InferenceResult(
            content="",
            provider="ll_ol_fallback",
            model=request.model or self.llama_cpp_model,
            status="failed",
            error=ol_cpu_result.error or ol_gpu_result.error or ll_result.error or "llama_cpp, ollama_gpu and ollama_cpu failed",
            stop_reason="error",
            metadata={
                "primary_provider": "llama_cpp",
                "fallback_provider": "ollama_gpu",
                "tertiary_fallback_provider": "ollama_cpu",
                "primary_error": ll_result.error,
                "fallback_error": ol_gpu_result.error,
                "tertiary_fallback_error": ol_cpu_result.error,
                "primary_metadata": ll_result.metadata or {},
                "fallback_metadata": ol_gpu_result.metadata or {},
                "tertiary_fallback_metadata": ol_cpu_result.metadata or {},
            },
        )

    async def _infer_hybrid(self, request: InferenceRequest) -> InferenceResult:
        async def _runner(coro):
            try:
                return await coro
            except Exception as exc:
                return InferenceResult(
                    content="",
                    provider="hybrid",
                    model=request.model or "hybrid",
                    status="failed",
                    error=str(exc),
                    stop_reason="error",
                )

        ollama_task = asyncio.create_task(_runner(self._infer_ollama(request)), name="ollama")
        openvino_task = asyncio.create_task(_runner(self._infer_openvino(request)), name="openvino")
        task_map = {ollama_task: "ollama", openvino_task: "openvino"}

        pending = {ollama_task, openvino_task}
        failures: Dict[str, InferenceResult] = {}
        winner = None

        while pending and winner is None:
            done, pending = await asyncio.wait(
                pending,
                timeout=120,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not done:
                break

            for finished in done:
                result = finished.result()
                provider_name = task_map.get(finished, result.provider)
                if result.stop_reason == "error" or result.status != "success":
                    failures[provider_name] = result
                    continue
                winner = result
                break

        cancelled_providers = []
        for task in pending:
            task.cancel()
            cancelled_providers.append(task_map.get(task, "unknown"))
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        if winner is None and failures:
            last_failure = next(reversed(failures.values()))
            return InferenceResult(
                content="",
                provider="hybrid",
                model=request.model or "hybrid",
                winner_provider=last_failure.provider,
                status="failed",
                error=last_failure.error or last_failure.content or "all providers failed",
                stop_reason="error",
                metadata={
                    "cancelled_providers": cancelled_providers,
                    "failed_providers": list(failures.keys()),
                },
            )

        if winner is None:
            return InferenceResult(
                content="",
                provider="hybrid",
                model=request.model or "hybrid",
                status="timeout",
                error="timeout waiting for providers",
                stop_reason="timeout",
                metadata={"cancelled_providers": cancelled_providers},
            )

        return InferenceResult(
            content=winner.content,
            provider="hybrid",
            model=winner.model,
            ttft_ms=winner.ttft_ms,
            gen_ms=winner.gen_ms,
            load_ms=winner.load_ms,
            winner_provider=winner.provider,
            stop_reason=winner.stop_reason,
            metadata={
                "cancelled_providers": cancelled_providers,
                "failed_providers": list(failures.keys()),
                **winner.metadata,
            },
        )

    def normalize_result(self, result: InferenceResult) -> InferenceNormalizedResponse:
        return self.normalizer.to_final(result)

    def to_stream_events(
        self,
        result: InferenceResult,
        *,
        run_id: str | None = None,
        chunk_size: int = 120,
    ) -> list[InferenceStreamEvent]:
        return self.normalizer.to_stream_events(result, run_id=run_id, chunk_size=chunk_size)
