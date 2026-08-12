"""Inference invocation adapters.

Provides a stable orchestrator-facing boundary for inference invocation.
Direct mode is active now; queue mode is scaffolded for next phase.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Optional
from uuid import uuid4

from services.config import Settings
from services.contracts import InferenceRequest, InferenceResult

from .queue import RedisStreamsInferenceQueueClient


class InferenceInvocationAdapter(ABC):
    """Boundary for orchestrator -> inference invocation."""

    @abstractmethod
    async def infer(self, request: InferenceRequest) -> InferenceResult:
        pass


class DirectInferenceInvoker(InferenceInvocationAdapter):
    """Direct in-process invocation adapter."""

    def __init__(self, inference_gateway: Any):
        self.inference_gateway = inference_gateway

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        result = await self.inference_gateway.infer(request)
        metadata = dict(result.metadata or {})
        metadata.setdefault("invocation_mode", "direct")
        result.metadata = metadata
        return result


class QueueReadyInferenceInvoker(InferenceInvocationAdapter):
    """Queue-ready invocation adapter scaffold."""

    def __init__(
        self,
        *,
        direct_gateway: Any | None = None,
        queue_client: Any | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 1,
        retry_backoff_seconds: float = 0.05,
        enable_fallback: bool = True,
    ):
        self.direct_gateway = direct_gateway
        self.queue_client = queue_client
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_backoff_seconds = retry_backoff_seconds
        self.enable_fallback = enable_fallback

    async def _queue_roundtrip(self, payload: dict[str, Any], *, correlation_id: str) -> dict[str, Any]:
        if self.queue_client is None:
            raise RuntimeError("queue_client is not configured")

        if hasattr(self.queue_client, "request_response"):
            return await self.queue_client.request_response(
                payload,
                correlation_id=correlation_id,
                timeout_seconds=self.timeout_seconds,
            )

        if hasattr(self.queue_client, "enqueue") and hasattr(self.queue_client, "wait_for_result"):
            await self.queue_client.enqueue(payload, correlation_id=correlation_id)
            return await self.queue_client.wait_for_result(
                correlation_id=correlation_id,
                timeout_seconds=self.timeout_seconds,
            )

        raise RuntimeError(
            "queue_client must implement request_response(...) or enqueue(...)+wait_for_result(...)"
        )

    async def infer(self, request: InferenceRequest) -> InferenceResult:
        if self.queue_client is not None:
            payload = request.model_dump()
            errors: list[str] = []

            for attempt in range(self.max_retries + 1):
                correlation_id = str(uuid4())
                try:
                    raw = await asyncio.wait_for(
                        self._queue_roundtrip(payload, correlation_id=correlation_id),
                        timeout=self.timeout_seconds,
                    )
                    result = InferenceResult.model_validate(raw)
                    metadata = dict(result.metadata or {})
                    metadata.setdefault("invocation_mode", "queue")
                    metadata.setdefault("queue_correlation_id", correlation_id)
                    metadata.setdefault("queue_attempt", attempt + 1)
                    result.metadata = metadata
                    return result
                except asyncio.TimeoutError:
                    errors.append(f"timeout on queue attempt {attempt + 1}")
                except Exception as exc:
                    errors.append(f"queue attempt {attempt + 1} failed: {exc}")

                if attempt < self.max_retries:
                    await asyncio.sleep(self.retry_backoff_seconds)

            if self.direct_gateway is not None and self.enable_fallback:
                result = await self.direct_gateway.infer(request)
                metadata = dict(result.metadata or {})
                metadata.setdefault("invocation_mode", "queue_fallback_direct")
                metadata.setdefault("queue_errors", errors)
                result.metadata = metadata
                return result

            timeout_seen = any("timeout" in err for err in errors)
            return InferenceResult(
                content="",
                provider=request.provider or "hybrid",
                model=request.model or "queue-default",
                status="timeout" if timeout_seen else "failed",
                error=errors[-1] if errors else "queue inference failed",
                stop_reason="timeout" if timeout_seen else "error",
                metadata={
                    "invocation_mode": "queue",
                    "queue_errors": errors,
                },
            )

        if self.direct_gateway is not None:
            result = await self.direct_gateway.infer(request)
            metadata = dict(result.metadata or {})
            metadata.setdefault("invocation_mode", "queue_fallback_direct")
            result.metadata = metadata
            return result

        raise RuntimeError("Queue inference invocation requires queue_client or direct_gateway fallback")


def ensure_inference_invoker(
    inference_dependency: Any,
    *,
    mode: Optional[str] = None,
) -> InferenceInvocationAdapter:
    """Wrap inference dependency in invocation adapter based on selected mode."""
    if isinstance(inference_dependency, InferenceInvocationAdapter):
        return inference_dependency

    selected_mode = (mode or "direct").strip().lower()
    if selected_mode == "queue":
        queue_client = None
        if Settings.REDIS_URL:
            queue_client = RedisStreamsInferenceQueueClient(redis_url=Settings.REDIS_URL)
        return QueueReadyInferenceInvoker(
            direct_gateway=inference_dependency,
            queue_client=queue_client,
        )

    return DirectInferenceInvoker(inference_dependency)
