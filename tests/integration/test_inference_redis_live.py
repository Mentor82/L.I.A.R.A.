"""Optional live Redis Streams inference transport test."""

import asyncio
import os
import uuid

import pytest

from services.inference.queue import (
    RedisStreamsInferenceQueueClient,
    RedisStreamsInferenceWorker,
)


RUN_LIVE_INFERENCE_QUEUE_TESTS = os.getenv("RUN_LIVE_INFERENCE_QUEUE_TESTS") == "1"
REDIS_URL = os.getenv("REDIS_URL")

pytestmark = pytest.mark.skipif(
    not RUN_LIVE_INFERENCE_QUEUE_TESTS or not REDIS_URL,
    reason="live inference queue test requires RUN_LIVE_INFERENCE_QUEUE_TESTS=1 plus REDIS_URL",
)


@pytest.mark.asyncio
class TestLiveInferenceRedisTransport:
    async def test_redis_streams_round_trip_against_live_redis(self):
        class FakeGateway:
            async def infer(self, request):
                return {
                    "content": f"live:{request.prompt}",
                    "provider": request.provider,
                    "model": request.model or "live-model",
                    "status": "success",
                    "stop_reason": "stop",
                    "metadata": {"worker": "live"},
                }

        suffix = uuid.uuid4().hex[:8]
        request_stream = f"liara:test:inference:req:{suffix}"
        response_prefix = f"liara:test:inference:resp:{suffix}"
        consumer_group = f"liara-test-workers-{suffix}"

        queue_client = RedisStreamsInferenceQueueClient(
            redis_url=REDIS_URL,
            request_stream=request_stream,
            response_stream_prefix=response_prefix,
            block_ms=25,
        )
        worker = RedisStreamsInferenceWorker(
            FakeGateway(),
            redis_url=REDIS_URL,
            request_stream=request_stream,
            consumer_group=consumer_group,
            consumer_name="live-worker",
            block_ms=25,
        )

        worker_task = asyncio.create_task(worker.process_next(timeout_seconds=0.5))
        try:
            result = await queue_client.request_response(
                {"prompt": "ping", "provider": "ollama", "model": "demo"},
                correlation_id=f"corr-{suffix}",
                timeout_seconds=1.0,
            )
            assert await worker_task is True
            assert result["content"] == "live:ping"
            assert result["metadata"]["worker"] == "live"
        finally:
            worker_task.cancel()
            try:
                await worker_task
            except asyncio.CancelledError:
                pass
            await queue_client.close()
            await worker.close()