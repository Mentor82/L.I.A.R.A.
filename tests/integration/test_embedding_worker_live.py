"""Optional real live test for Redis Streams embedding worker."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import uuid

import pytest


RUN_LIVE_EMBEDDING_WORKER_TESTS = os.getenv("RUN_LIVE_EMBEDDING_WORKER_TESTS") == "1"
REDIS_URL = os.getenv("REDIS_URL", "redis://:liara2026@127.0.0.1:6380/0")
EMBEDDING_MODEL_DIR = os.getenv("EMBEDDING_MODEL_DIR")
EMBEDDING_MODEL_ID = os.getenv("EMBEDDING_MODEL_ID", "OpenVINO/Qwen3-Embedding-0.6B-fp16-ov")
EMBEDDING_DEVICE = os.getenv("EMBEDDING_DEVICE", "CPU")

pytestmark = pytest.mark.skipif(
    not RUN_LIVE_EMBEDDING_WORKER_TESTS or not EMBEDDING_MODEL_DIR,
    reason=(
        "live embedding worker test requires RUN_LIVE_EMBEDDING_WORKER_TESTS=1 "
        "plus EMBEDDING_MODEL_DIR"
    ),
)


def _load_worker_module():
    worker_path = Path(__file__).resolve().parents[2] / "workers" / "embedding-worker" / "worker.py"
    spec = importlib.util.spec_from_file_location("liara_embedding_worker", worker_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load worker module from {worker_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
class TestLiveEmbeddingWorker:
    async def test_embedding_worker_processes_real_request(self):
        module = _load_worker_module()
        OpenVINOEmbeddingEngine = module.OpenVINOEmbeddingEngine
        RedisStreamsEmbeddingWorker = module.RedisStreamsEmbeddingWorker

        from redis import asyncio as redis_asyncio

        suffix = uuid.uuid4().hex[:8]
        request_stream = f"liara:test:embedding:req:{suffix}"
        response_stream = f"liara:test:embedding:resp:{suffix}"
        correlation_id = f"corr-{suffix}"
        consumer_group = f"liara-test-embedding-workers-{suffix}"

        redis_client = redis_asyncio.from_url(REDIS_URL, decode_responses=False)
        worker = RedisStreamsEmbeddingWorker(
            OpenVINOEmbeddingEngine(
                model_id=EMBEDDING_MODEL_ID,
                model_dir=EMBEDDING_MODEL_DIR,
                device=EMBEDDING_DEVICE,
            ),
            redis_url=REDIS_URL,
            request_stream=request_stream,
            response_stream_prefix=f"liara:test:embedding:resp-prefix:{suffix}",
            consumer_group=consumer_group,
            consumer_name="live-embedding-worker",
            block_ms=100,
        )

        try:
            await redis_client.xadd(
                request_stream,
                {
                    "correlation_id": correlation_id,
                    "reply_stream": response_stream,
                    "request": json.dumps(
                        {
                            "input_text": "real embedding worker validation for qdrant retrieval",
                            "model": EMBEDDING_MODEL_ID,
                            "normalize": True,
                            "metadata": {"topic": "live-worker"},
                        }
                    ),
                },
            )

            processed = await worker.process_next(timeout_seconds=90.0)
            assert processed is True

            messages = await redis_client.xread({response_stream: "0-0"}, count=1, block=5000)
            assert messages

            fields = messages[0][1][0][1]
            result_raw = fields[b"result"] if isinstance(fields.get(b"result"), (bytes, bytearray)) else fields["result"]
            payload = json.loads(result_raw.decode("utf-8") if isinstance(result_raw, (bytes, bytearray)) else result_raw)

            assert payload["status"]["status"] == "success"
            assert payload["item"] is not None
            assert payload["item"]["dimensions"] > 0
            assert len(payload["item"]["vector"]) == payload["item"]["dimensions"]
            assert payload["item"]["metadata"].get("backend") == "openvino"
            assert payload["item"]["metadata"].get("fallback") is False
        finally:
            await worker.close()
            await redis_client.delete(request_stream)
            await redis_client.delete(response_stream)
            await redis_client.aclose()
