import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from services.api.workload import WorkloadMiddleware, WorkloadTracker
from services.heartbeat.adapters import NativeSystemAdapter
from services.heartbeat.core import HeartbeatInstance
from services.contracts.heartbeat import ObservationBatch
from services.self_observer.probes import SelfObserverProbes


@pytest.mark.parametrize("failure", [None, RuntimeError, asyncio.CancelledError])
async def test_count_covers_concurrent_streams_and_always_cleans_up(failure):
    tracker = WorkloadTracker()
    entered = asyncio.Queue()
    release = asyncio.Event()

    async def app(scope, receive, send):
        if scope["path"] == "/health/workload":
            return
        await entered.put(True)
        await release.wait()  # Includes time after response headers, until stream ends.
        if failure:
            raise failure()

    middleware = WorkloadMiddleware(app, tracker)
    tasks = [asyncio.create_task(middleware(
        {"type": "http", "method": "POST", "path": "/chat/stream"}, None, None,
    )) for _ in range(2)]
    await entered.get()
    await entered.get()
    assert tracker.snapshot()["active_work"] == 2
    await middleware({"type": "http", "method": "GET", "path": "/health/workload"}, None, None)
    assert tracker.snapshot()["active_work"] == 2
    release.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert tracker.snapshot()["active_work"] == 0


@pytest.mark.parametrize("active", [0, 2])
def test_source_count_reaches_observer_without_defaulting(monkeypatch, active):
    now = datetime.now(UTC)
    payload = {"active_work": active, "observed_at": now.isoformat(), "scope": "single_api_worker"}
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(
        200, json=payload, request=httpx.Request("GET", "http://local/health/workload"),
    ))
    observations = NativeSystemAdapter()._workload_observations(now)
    instance = HeartbeatInstance(node_id="test")
    instance.ingest(ObservationBatch(node_id="test", observations=observations))
    curve = instance.curve(window_seconds=60)
    value, timestamp, signal = SelfObserverProbes()._active_work(curve.model_dump(mode="json"), now)
    assert value == active
    assert timestamp == now
    assert signal is None


@pytest.mark.parametrize("case", ["missing", "stale", "future", "negative", "boolean", "naive", "scope", "offline"])
def test_invalid_or_missing_workload_never_becomes_idle(monkeypatch, case):
    now = datetime.now(UTC)
    payload = {"active_work": 0, "observed_at": now.isoformat(), "scope": "single_api_worker"}
    if case == "missing":
        del payload["active_work"]
    elif case in {"stale", "future"}:
        payload["observed_at"] = (now + timedelta(seconds=-16 if case == "stale" else 6)).isoformat()
    elif case == "negative":
        payload["active_work"] = -1
    elif case == "boolean":
        payload["active_work"] = False
    elif case == "naive":
        payload["observed_at"] = now.replace(tzinfo=None).isoformat()
    elif case == "scope":
        payload["scope"] = "unknown"

    def get(*args, **kwargs):
        if case == "offline":
            raise httpx.ConnectError("offline")
        return httpx.Response(200, json=payload, request=httpx.Request("GET", "http://local"))

    monkeypatch.setattr(httpx, "get", get)
    assert NativeSystemAdapter()._workload_observations(now) == []
