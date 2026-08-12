"""Live integration test for the Redis Streams LLM inference worker.

Tests the full path:
  Redis request → RedisStreamsInferenceWorker.process_next() → InferenceGateway → Ollama → Redis response

Requires:
  RUN_LIVE_LLM_WORKER_TESTS=1
  REDIS_URL (e.g. redis://:password@127.0.0.1:6380/0)
  Ollama running on 127.0.0.1:11434 with qwen2.5:3b (or OLLAMA_MODEL override)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid

import pytest

RUN_LIVE = os.getenv("RUN_LIVE_LLM_WORKER_TESTS") == "1"
REDIS_URL = os.getenv("REDIS_URL")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1")
OLLAMA_PORT = int(os.getenv("OLLAMA_PORT", "11434"))
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b")

pytestmark = pytest.mark.skipif(
    not RUN_LIVE or not REDIS_URL,
    reason="live LLM worker test requires RUN_LIVE_LLM_WORKER_TESTS=1 and REDIS_URL",
)

REQUEST_STREAM = "liara:test:llm:requests"
RESPONSE_STREAM_PREFIX = "liara:test:llm:responses"


async def _flush_streams(client):
    """Remove test streams to avoid leftover pollution between runs."""
    try:
        await client.delete(REQUEST_STREAM)
    except Exception:
        pass


@pytest.mark.asyncio
class TestLiveLLMWorker:
    async def test_worker_processes_request_and_returns_real_llm_response(self):
        """Full round-trip: enqueue → worker.process_next() → Ollama → dequeue response."""
        from redis import asyncio as aioredis

        from services.contracts import InferenceRequest
        from services.inference.gateway import InferenceGateway
        from services.inference.queue import (
            RedisStreamsInferenceQueueClient,
            RedisStreamsInferenceWorker,
        )

        client = aioredis.from_url(REDIS_URL, decode_responses=False)
        try:
            await _flush_streams(client)

            gateway = InferenceGateway(config={
                "DEFAULT_LLM_PROVIDER": "ollama",
                "OLLAMA_HOST": OLLAMA_HOST,
                "OLLAMA_PORT": OLLAMA_PORT,
                "OLLAMA_MODEL": OLLAMA_MODEL,
            })

            worker = RedisStreamsInferenceWorker(
                inference_gateway=gateway,
                redis_url=REDIS_URL,
                request_stream=REQUEST_STREAM,
                consumer_group="test-llm-worker-group",
                consumer_name="test-worker-1",
                block_ms=500,
                client=client,
            )

            queue_client = RedisStreamsInferenceQueueClient(
                redis_url=REDIS_URL,
                request_stream=REQUEST_STREAM,
                response_stream_prefix=RESPONSE_STREAM_PREFIX,
                client=client,
            )

            correlation_id = str(uuid.uuid4())
            reply_stream = queue_client._reply_stream(correlation_id)

            # Enqueue a real inference request
            request = InferenceRequest(
                prompt="Reply with exactly three words: 'yes it works'",
                provider="ollama",
                max_tokens=16,
            )
            await queue_client.enqueue(request.model_dump(), correlation_id=correlation_id)

            # Worker processes it — calls Ollama, writes result back to reply stream
            t0 = time.perf_counter()
            processed = await worker.process_next(timeout_seconds=30.0)
            elapsed = time.perf_counter() - t0

            assert processed is True, "Worker reported no message processed — check Redis connection and stream"

            # Read the response directly from the reply stream
            messages = await client.xread({reply_stream: "0-0"}, count=1)
            assert messages, "No response written to reply stream by worker"

            _stream, entries = messages[0]
            _msg_id, fields = entries[0]
            decoded = {
                (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                for k, v in fields.items()
            }

            assert decoded.get("correlation_id") == correlation_id, "correlation_id mismatch in response"
            result = json.loads(decoded["result"])

            # Contract assertions
            assert result["status"] == "success", f"LLM returned non-success: {result.get('error')}"
            assert isinstance(result["content"], str) and len(result["content"]) > 0, "Empty content from LLM"
            assert result["provider"] == "ollama"
            assert result["model"] == OLLAMA_MODEL
            assert isinstance(result["ttft_ms"], (int, float)) and result["ttft_ms"] > 0
            assert isinstance(result["gen_ms"], (int, float)) and result["gen_ms"] > 0

            print(f"\n  Worker round-trip : {elapsed*1000:.0f} ms")
            print(f"  LLM content       : {result['content'][:80]!r}")
            print(f"  ttft_ms           : {result['ttft_ms']:.1f}")
            print(f"  gen_ms            : {result['gen_ms']:.1f}")

        finally:
            await _flush_streams(client)
            aclose = getattr(client, "aclose", None) or getattr(client, "close", None)
            if callable(aclose):
                await aclose()

    async def test_worker_writes_error_result_on_bad_request(self):
        """Worker must not crash on malformed payload — error goes back to reply stream."""
        from redis import asyncio as aioredis
        from services.inference.queue import RedisStreamsInferenceWorker

        class AlwaysFailGateway:
            async def infer(self, request):
                raise RuntimeError("simulated gateway failure")

        client = aioredis.from_url(REDIS_URL, decode_responses=False)
        try:
            await _flush_streams(client)

            worker = RedisStreamsInferenceWorker(
                inference_gateway=AlwaysFailGateway(),
                redis_url=REDIS_URL,
                request_stream=REQUEST_STREAM,
                consumer_group="test-llm-error-group",
                consumer_name="test-worker-err",
                block_ms=500,
                client=client,
            )

            correlation_id = str(uuid.uuid4())
            reply_stream = f"{RESPONSE_STREAM_PREFIX}:{correlation_id}"

            # Enqueue a valid-looking message manually
            await client.xadd(REQUEST_STREAM, {
                "correlation_id": correlation_id,
                "reply_stream": reply_stream,
                "request": json.dumps({"prompt": "hello", "provider": "ollama"}),
            })

            processed = await worker.process_next(timeout_seconds=5.0)
            assert processed is True

            messages = await client.xread({reply_stream: "0-0"}, count=1)
            assert messages, "Worker did not write error response to reply stream"

            _stream, entries = messages[0]
            _msg_id, fields = entries[0]
            decoded = {
                (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
                for k, v in fields.items()
            }
            result = json.loads(decoded["result"])

            assert result["status"] == "failed"
            assert "simulated gateway failure" in result.get("error", "")

        finally:
            await _flush_streams(client)
            aclose = getattr(client, "aclose", None) or getattr(client, "close", None)
            if callable(aclose):
                await aclose()
