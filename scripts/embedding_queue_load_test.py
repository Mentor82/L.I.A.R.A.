from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
import uuid


async def _run_load_test(args: argparse.Namespace) -> int:
    try:
        from redis import asyncio as redis_asyncio  # type: ignore
    except ImportError:
        print("LOAD_FAIL: redis package is required")
        return 2

    redis_url = args.redis_url or os.getenv("REDIS_URL", "")
    if not redis_url:
        print("LOAD_FAIL: REDIS_URL is required")
        return 2

    request_stream = args.request_stream or os.getenv("EMBEDDING_QUEUE_REQUEST_STREAM", "liara:embedding:requests")
    response_prefix = args.response_prefix or os.getenv("EMBEDDING_QUEUE_RESPONSE_STREAM_PREFIX", "liara:embedding:responses")

    client = redis_asyncio.from_url(redis_url, decode_responses=True)
    latencies: list[float] = []
    failures = 0
    fallback_count = 0
    truncation_count = 0
    try:
        for idx in range(args.requests):
            correlation_id = f"load-{uuid.uuid4()}"
            reply_stream = f"{response_prefix}:{correlation_id}"
            payload = {
                "input_text": f"load test request {idx} " + ("x " * args.words_per_request),
                "normalize": True,
                "metadata": {"source": "embedding_queue_load_test"},
            }
            started = time.perf_counter()
            await client.xadd(
                request_stream,
                {
                    "correlation_id": correlation_id,
                    "reply_stream": reply_stream,
                    "request": json.dumps(payload),
                },
            )
            response = await client.xread({reply_stream: "0-0"}, count=1, block=max(1, int(args.timeout * 1000)))
            latency_ms = (time.perf_counter() - started) * 1000.0
            latencies.append(latency_ms)
            if not response:
                failures += 1
                continue
            _stream_name, entries = response[0]
            _message_id, fields = entries[0]
            result = json.loads(fields.get("result", "{}"))
            status = (result.get("status") or {})
            item = result.get("item") or {}
            metadata = item.get("metadata") or {}
            if status.get("status") != "success":
                failures += 1
            if status.get("degraded"):
                fallback_count += 1
            if metadata.get("input_truncated"):
                truncation_count += 1

        total = max(1, args.requests)
        print(
            json.dumps(
                {
                    "requests": args.requests,
                    "avg_latency_ms": round(statistics.mean(latencies), 3) if latencies else 0.0,
                    "max_latency_ms": round(max(latencies), 3) if latencies else 0.0,
                    "failure_rate": round(failures / total, 6),
                    "fallback_rate": round(fallback_count / total, 6),
                    "truncation_rate": round(truncation_count / total, 6),
                },
                indent=2,
            )
        )
        return 0 if failures == 0 else 1
    finally:
        await client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Redis-stream embedding queue load test.")
    parser.add_argument("--requests", type=int, default=10, help="Number of queue requests to send")
    parser.add_argument("--timeout", type=float, default=10.0, help="Per-request timeout in seconds")
    parser.add_argument("--words-per-request", type=int, default=32, help="Approximate request size in words")
    parser.add_argument("--redis-url", default="", help="Override REDIS_URL")
    parser.add_argument("--request-stream", default="", help="Override request stream name")
    parser.add_argument("--response-prefix", default="", help="Override response stream prefix")
    args = parser.parse_args()
    return asyncio.run(_run_load_test(args))


if __name__ == "__main__":
    raise SystemExit(main())