"""Redis Streams transport for decoupled inference execution."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from services.config import Settings
from services.contracts import InferenceRequest, InferenceResult


class RedisStreamsInferenceQueueClient:
    """Request-response transport over Redis Streams."""

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        request_stream: str | None = None,
        response_stream_prefix: str | None = None,
        block_ms: int | None = None,
        client: Any = None,
    ):
        self.redis_url = redis_url or Settings.REDIS_URL
        self.request_stream = request_stream or Settings.INFERENCE_QUEUE_REQUEST_STREAM
        self.response_stream_prefix = response_stream_prefix or Settings.INFERENCE_QUEUE_RESPONSE_STREAM_PREFIX
        self.block_ms = block_ms or Settings.INFERENCE_QUEUE_BLOCK_MS
        self._client = client
        self._owns_client = False

        if self._client is None and not self.redis_url:
            raise RuntimeError("REDIS_URL is not configured for RedisStreamsInferenceQueueClient")

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            from redis import asyncio as redis_asyncio  # type: ignore
        except ImportError as exc:
            raise RuntimeError("redis package is required for RedisStreamsInferenceQueueClient") from exc

        self._client = redis_asyncio.from_url(self.redis_url, decode_responses=False)
        self._owns_client = True
        return self._client

    async def close(self) -> None:
        if self._client is None or not self._owns_client:
            return
        try:
            aclose = getattr(self._client, "aclose", None)
            if callable(aclose):
                await aclose()
            else:
                await self._client.close()
        finally:
            self._client = None
            self._owns_client = False

    def _reply_stream(self, correlation_id: str) -> str:
        return f"{self.response_stream_prefix}:{correlation_id}"

    @staticmethod
    def _decode_text(value: Any) -> str:
        if isinstance(value, (bytes, bytearray)):
            return value.decode("utf-8")
        return str(value)

    @classmethod
    def _decode_fields(cls, fields: dict[Any, Any]) -> dict[str, str]:
        return {cls._decode_text(key): cls._decode_text(value) for key, value in fields.items()}

    async def enqueue(self, payload: dict[str, Any], *, correlation_id: str) -> str:
        client = await self._ensure_client()
        reply_stream = self._reply_stream(correlation_id)
        await client.xadd(
            self.request_stream,
            {
                "correlation_id": correlation_id,
                "reply_stream": reply_stream,
                "request": json.dumps(payload),
            },
        )
        return reply_stream

    async def wait_for_result(
        self,
        *,
        correlation_id: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        client = await self._ensure_client()
        response_stream = self._reply_stream(correlation_id)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        last_id = "0-0"

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"timeout waiting for queue result {correlation_id}")

            block_ms = max(1, min(self.block_ms, int(remaining * 1000)))
            messages = await client.xread({response_stream: last_id}, count=1, block=block_ms)
            if not messages:
                continue

            for _stream_name, entries in messages:
                for message_id, fields in entries:
                    last_id = self._decode_text(message_id)
                    decoded = self._decode_fields(fields)
                    if decoded.get("correlation_id") != correlation_id:
                        continue
                    raw_result = decoded.get("result")
                    if raw_result is None:
                        raise RuntimeError("queue response missing result payload")
                    try:
                        return json.loads(raw_result)
                    finally:
                        delete = getattr(client, "delete", None)
                        if callable(delete):
                            await delete(response_stream)

    async def request_response(
        self,
        payload: dict[str, Any],
        *,
        correlation_id: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        await self.enqueue(payload, correlation_id=correlation_id)
        return await self.wait_for_result(correlation_id=correlation_id, timeout_seconds=timeout_seconds)


class RedisStreamsInferenceWorker:
    """Minimal Redis Streams worker that consumes inference requests."""

    def __init__(
        self,
        inference_gateway: Any,
        redis_url: str | None = None,
        *,
        request_stream: str | None = None,
        consumer_group: str | None = None,
        consumer_name: str = "worker-1",
        block_ms: int | None = None,
        client: Any = None,
    ):
        self.inference_gateway = inference_gateway
        self.redis_url = redis_url or Settings.REDIS_URL
        self.request_stream = request_stream or Settings.INFERENCE_QUEUE_REQUEST_STREAM
        self.consumer_group = consumer_group or Settings.INFERENCE_QUEUE_CONSUMER_GROUP
        self.consumer_name = consumer_name
        self.block_ms = block_ms or Settings.INFERENCE_QUEUE_BLOCK_MS
        self._client = client
        self._owns_client = False
        self._group_ready = False

        if self._client is None and not self.redis_url:
            raise RuntimeError("REDIS_URL is not configured for RedisStreamsInferenceWorker")

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            from redis import asyncio as redis_asyncio  # type: ignore
        except ImportError as exc:
            raise RuntimeError("redis package is required for RedisStreamsInferenceWorker") from exc

        self._client = redis_asyncio.from_url(self.redis_url, decode_responses=False)
        self._owns_client = True
        return self._client

    async def close(self) -> None:
        if self._client is None or not self._owns_client:
            return
        try:
            aclose = getattr(self._client, "aclose", None)
            if callable(aclose):
                await aclose()
            else:
                await self._client.close()
        finally:
            self._client = None
            self._owns_client = False
            self._group_ready = False

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        client = await self._ensure_client()
        try:
            await client.xgroup_create(self.request_stream, self.consumer_group, id="0-0", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    async def process_next(self, timeout_seconds: float = 1.0) -> bool:
        await self._ensure_group()
        client = await self._ensure_client()
        block_ms = max(1, min(self.block_ms, int(timeout_seconds * 1000)))
        messages = await client.xreadgroup(
            self.consumer_group,
            self.consumer_name,
            {self.request_stream: ">"},
            count=1,
            block=block_ms,
        )
        if not messages:
            return False

        for _stream_name, entries in messages:
            for message_id, fields in entries:
                decoded = RedisStreamsInferenceQueueClient._decode_fields(fields)
                correlation_id = decoded.get("correlation_id", "")
                reply_stream = decoded.get("reply_stream", "")
                raw_request = decoded.get("request", "{}")

                try:
                    request = InferenceRequest.model_validate(json.loads(raw_request))
                    raw_result = await self.inference_gateway.infer(request)
                    result = InferenceResult.model_validate(raw_result)
                except Exception as exc:
                    result = InferenceResult(
                        content="",
                        provider="hybrid",
                        model="queue-default",
                        status="failed",
                        error=str(exc),
                        stop_reason="error",
                        metadata={"invocation_mode": "queue_worker"},
                    )

                if reply_stream:
                    await client.xadd(
                        reply_stream,
                        {
                            "correlation_id": correlation_id,
                            "result": json.dumps(result.model_dump()),
                        },
                    )

                await client.xack(self.request_stream, self.consumer_group, message_id)
                return True

        return False

    async def startup_smoke_test(self, verbose: bool = True) -> None:
        """
        Verify ll_ol_fallback inference chain on startup.
        Tests primary (llama_cpp) and fallback (ollama) availability.
        """
        import logging
        logger = logging.getLogger(__name__)
        
        try:
            # Test with explicit ll_ol_fallback provider
            request = InferenceRequest(
                prompt="OK",
                provider="ll_ol_fallback",
                max_tokens=4,
            )
            
            if verbose:
                logger.info("[WORKER STARTUP] Testing inference stack with ll_ol_fallback...")
            
            result = await self.inference_gateway.infer(request)
            
            # Convert to dict if it's a Pydantic model
            if hasattr(result, "model_dump"):
                result_dict = result.model_dump()
            else:
                result_dict = result if isinstance(result, dict) else {}
            
            # Determine which provider actually handled the request
            winner = result_dict.get("winner_provider")
            provider_used = winner or result_dict.get("provider", "unknown")
            primary_error = result_dict.get("metadata", {}).get("primary_error")
            fallback_error = result_dict.get("metadata", {}).get("fallback_error")
            status = result_dict.get("status", "unknown")
            
            if verbose:
                if status == "success":
                    if winner == "llama_cpp":
                        logger.info(f"[WORKER STARTUP] ✓ Primary (llama_cpp) active and responding")
                    elif winner == "ollama":
                        logger.info(f"[WORKER STARTUP] ⚠ Fallback (ollama) used (llama_cpp unavailable)")
                        if primary_error:
                            logger.info(f"[WORKER STARTUP]   Primary error: {str(primary_error)[:100]}")
                    else:
                        logger.info(f"[WORKER STARTUP] ✓ Inference successful via {provider_used}")
                else:
                    # Both failed
                    logger.warning(f"[WORKER STARTUP] ✗ Both providers failed (stack degraded)")
                    if primary_error:
                        logger.warning(f"[WORKER STARTUP]   llama_cpp: {str(primary_error)[:100]}")
                    if fallback_error:
                        logger.warning(f"[WORKER STARTUP]   ollama: {str(fallback_error)[:100]}")
                    # Don't raise - stack is still initialized
                
                logger.info(f"[WORKER STARTUP] Stack ready: provider={provider_used}, winner={winner}, status={status}")
        except Exception as exc:
            if verbose:
                logger.error(f"[WORKER STARTUP] ✗ Inference stack test failed: {exc}", exc_info=True)
            raise

    async def serve_forever(
        self,
        *,
        stop_event: asyncio.Event | None = None,
        poll_timeout_seconds: float = 1.0,
    ) -> None:
        while stop_event is None or not stop_event.is_set():
            await self.process_next(timeout_seconds=poll_timeout_seconds)
