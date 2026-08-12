"""Unit tests for Redis Streams inference transport."""

import asyncio

import pytest

from services.inference.queue import (
    RedisStreamsInferenceQueueClient,
    RedisStreamsInferenceWorker,
)


class FakeRedisStreamsClient:
    def __init__(self):
        self.streams = {}
        self.events = {}
        self.counters = {}
        self.groups = {}
        self.acks = []

    def _event(self, stream):
        return self.events.setdefault(stream, asyncio.Event())

    def _entry_id(self, stream):
        next_id = self.counters.get(stream, 0) + 1
        self.counters[stream] = next_id
        return f"{next_id}-0"

    async def xadd(self, stream, fields):
        message_id = self._entry_id(stream)
        encoded = {
            str(key).encode("utf-8"): str(value).encode("utf-8")
            for key, value in fields.items()
        }
        self.streams.setdefault(stream, []).append((message_id.encode("utf-8"), encoded))
        self._event(stream).set()
        return message_id

    async def xread(self, streams, count=1, block=None):
        del count
        stream, last_id = next(iter(streams.items()))
        entries = self.streams.get(stream, [])
        pending = [entry for entry in entries if self._gt(entry[0].decode("utf-8"), last_id)]
        if pending:
            self._event(stream).clear()
            return [(stream.encode("utf-8"), pending[:1])]

        if block:
            try:
                await asyncio.wait_for(self._event(stream).wait(), timeout=block / 1000)
            except asyncio.TimeoutError:
                return []

        entries = self.streams.get(stream, [])
        pending = [entry for entry in entries if self._gt(entry[0].decode("utf-8"), last_id)]
        if pending:
            self._event(stream).clear()
            return [(stream.encode("utf-8"), pending[:1])]
        return []

    async def xgroup_create(self, stream, groupname, id="0-0", mkstream=False):
        if mkstream:
            self.streams.setdefault(stream, [])
        groups = self.groups.setdefault(stream, {})
        if groupname in groups:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        groups[groupname] = {"last_index": -1 if id == "0-0" else len(self.streams.get(stream, [])) - 1}

    async def xreadgroup(self, groupname, consumername, streams, count=1, block=None):
        del consumername, count
        stream, start = next(iter(streams.items()))
        if start != ">":
            raise AssertionError("fake client only supports '>' reads")
        state = self.groups.setdefault(stream, {}).setdefault(groupname, {"last_index": -1})
        entries = self.streams.get(stream, [])
        next_index = state["last_index"] + 1
        if next_index < len(entries):
            state["last_index"] = next_index
            self._event(stream).clear()
            return [(stream.encode("utf-8"), [entries[next_index]])]

        if block:
            try:
                await asyncio.wait_for(self._event(stream).wait(), timeout=block / 1000)
            except asyncio.TimeoutError:
                return []

        entries = self.streams.get(stream, [])
        next_index = state["last_index"] + 1
        if next_index < len(entries):
            state["last_index"] = next_index
            self._event(stream).clear()
            return [(stream.encode("utf-8"), [entries[next_index]])]
        return []

    async def xack(self, stream, groupname, message_id):
        self.acks.append((stream, groupname, message_id))
        return 1

    async def delete(self, stream):
        self.streams.pop(stream, None)
        return 1

    @staticmethod
    def _gt(left, right):
        left_major = int(left.split("-")[0])
        right_major = int(str(right).split("-")[0])
        return left_major > right_major


@pytest.mark.asyncio
class TestRedisStreamsInferenceTransport:
    async def test_request_response_round_trip_via_worker(self):
        class FakeGateway:
            async def infer(self, request):
                return {
                    "content": f"worker:{request.prompt}",
                    "provider": request.provider,
                    "model": request.model or "queue-model",
                    "status": "success",
                    "stop_reason": "stop",
                    "metadata": {"source": "worker"},
                }

        client = FakeRedisStreamsClient()
        queue_client = RedisStreamsInferenceQueueClient(
            client=client,
            request_stream="test:req",
            response_stream_prefix="test:resp",
        )
        worker = RedisStreamsInferenceWorker(
            FakeGateway(),
            client=client,
            request_stream="test:req",
            consumer_group="test-workers",
            consumer_name="worker-a",
        )

        worker_task = asyncio.create_task(worker.process_next(timeout_seconds=0.2))
        result = await queue_client.request_response(
            {"prompt": "hello", "provider": "ollama", "model": "demo"},
            correlation_id="corr-1",
            timeout_seconds=0.2,
        )

        assert await worker_task is True
        assert result["content"] == "worker:hello"
        assert result["metadata"]["source"] == "worker"
        assert client.acks == [("test:req", "test-workers", b"1-0")]

    async def test_wait_for_result_times_out_when_no_worker_replies(self):
        queue_client = RedisStreamsInferenceQueueClient(
            client=FakeRedisStreamsClient(),
            request_stream="test:req",
            response_stream_prefix="test:resp",
            block_ms=5,
        )

        await queue_client.enqueue({"prompt": "hello"}, correlation_id="corr-2")
        with pytest.raises(TimeoutError):
            await queue_client.wait_for_result(correlation_id="corr-2", timeout_seconds=0.02)

    async def test_worker_returns_failure_envelope_when_gateway_raises(self):
        class BrokenGateway:
            async def infer(self, request):
                del request
                raise RuntimeError("gateway failed")

        client = FakeRedisStreamsClient()
        queue_client = RedisStreamsInferenceQueueClient(
            client=client,
            request_stream="test:req",
            response_stream_prefix="test:resp",
        )
        worker = RedisStreamsInferenceWorker(
            BrokenGateway(),
            client=client,
            request_stream="test:req",
            consumer_group="test-workers",
            consumer_name="worker-a",
        )

        worker_task = asyncio.create_task(worker.process_next(timeout_seconds=0.2))
        result = await queue_client.request_response(
            {"prompt": "hello", "provider": "hybrid"},
            correlation_id="corr-3",
            timeout_seconds=0.2,
        )

        assert await worker_task is True
        assert result["status"] == "failed"
        assert result["error"] == "gateway failed"
        assert result["metadata"]["invocation_mode"] == "queue_worker"