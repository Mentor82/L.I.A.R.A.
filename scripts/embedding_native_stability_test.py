from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError


@dataclass
class Result:
    index: int
    ok: bool
    status: int | None
    latency_ms: float
    error: str | None = None
    dimensions: int | None = None


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib_request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return int(response.status), json.loads(raw) if raw else {}


def _get_json(url: str, timeout: float) -> tuple[int, dict[str, Any]]:
    with urllib_request.urlopen(url, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
        return int(response.status), json.loads(raw) if raw else {}


def _pid_from_lock(lock_path: Path) -> int:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        return int(payload.get("pid") or 0)
    except Exception:
        return 0


async def _one(
    *,
    index: int,
    base_url: str,
    timeout: float,
    words_per_request: int,
    semaphore: asyncio.Semaphore,
) -> Result:
    payload = {
        "input_text": f"native embedding stability request {index} " + ("liara " * words_per_request),
        "normalize": True,
        "metadata": {
            "source": "embedding_native_stability_test",
            "request_index": index,
        },
    }
    async with semaphore:
        started = time.perf_counter()
        try:
            status, data = await asyncio.to_thread(
                _post_json,
                f"{base_url.rstrip('/')}/embedding/generate",
                payload,
                timeout,
            )
        except HTTPError as exc:
            return Result(index=index, ok=False, status=exc.code, latency_ms=(time.perf_counter() - started) * 1000.0, error=str(exc))
        except (OSError, URLError, TimeoutError) as exc:
            return Result(index=index, ok=False, status=None, latency_ms=(time.perf_counter() - started) * 1000.0, error=str(exc))
        except Exception as exc:
            return Result(index=index, ok=False, status=None, latency_ms=(time.perf_counter() - started) * 1000.0, error=f"{type(exc).__name__}: {exc}")

        latency_ms = (time.perf_counter() - started) * 1000.0
        item = data.get("item") if isinstance(data.get("item"), dict) else {}
        vector = item.get("vector") if isinstance(item, dict) else data.get("embedding")
        declared_dimensions = item.get("dimensions") if isinstance(item, dict) else None
        dimensions = len(vector) if isinstance(vector, list) else declared_dimensions
        service_status = data.get("status") if isinstance(data.get("status"), dict) else {}
        ok = (
            status == 200
            and dimensions is not None
            and (not service_status or service_status.get("status") == "success")
        )
        return Result(index=index, ok=ok, status=status, latency_ms=latency_ms, dimensions=dimensions)


async def _run(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    lock_path = repo_root / "logs" / "service_locks" / "embedding.lock.json"
    pid_before = _pid_from_lock(lock_path)

    health_before_status, health_before = await asyncio.to_thread(
        _get_json,
        f"{args.base_url.rstrip('/')}/health",
        args.timeout,
    )

    semaphore = asyncio.Semaphore(max(1, args.concurrency))
    tasks = [
        _one(
            index=index,
            base_url=args.base_url,
            timeout=args.timeout,
            words_per_request=args.words_per_request,
            semaphore=semaphore,
        )
        for index in range(args.requests)
    ]
    results = await asyncio.gather(*tasks)

    pid_after = _pid_from_lock(lock_path)
    try:
        health_after_status, health_after = await asyncio.to_thread(
            _get_json,
            f"{args.base_url.rstrip('/')}/health",
            args.timeout,
        )
    except Exception as exc:
        health_after_status = None
        health_after = {"error": f"{type(exc).__name__}: {exc}"}

    failures = [result for result in results if not result.ok]
    latencies = [result.latency_ms for result in results]
    dimensions = sorted({result.dimensions for result in results if result.dimensions is not None})
    pid_stable = bool(pid_before and pid_before == pid_after)
    health_after_ready = bool(health_after.get("ready"))

    summary = {
        "base_url": args.base_url,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "words_per_request": args.words_per_request,
        "pid_before": pid_before,
        "pid_after": pid_after,
        "pid_stable": pid_stable,
        "health_before_status": health_before_status,
        "health_after_status": health_after_status,
        "health_after_ready": health_after_ready,
        "runtime": health_after.get("runtime") or health_after.get("runtime_backend") or health_before.get("runtime"),
        "device": health_after.get("device") or health_before.get("device"),
        "dimensions_seen": dimensions,
        "successes": args.requests - len(failures),
        "failures": len(failures),
        "failure_examples": [failure.__dict__ for failure in failures[:5]],
        "latency_ms": {
            "avg": round(statistics.mean(latencies), 3) if latencies else 0.0,
            "p95": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 3) if latencies else 0.0,
            "max": round(max(latencies), 3) if latencies else 0.0,
        },
    }
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0 if not failures and pid_stable and health_after_ready else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Direct HTTP stability test for the native embedding service.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8030")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--words-per-request", type=int, default=32)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
