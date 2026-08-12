"""FastAPI surface for the independent LIARA heartbeat instance."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query, Response

from services.contracts.heartbeat import HeartbeatSnapshot, ObservationBatch, StateCurve
from .adapters import JsonObservationAdapter, MappedCsvAdapter, NativeSystemAdapter, ObservationAdapter
from .core import HeartbeatInstance


def _configured_adapter() -> ObservationAdapter:
    adapter_name = os.getenv("LIARA_HEARTBEAT_ADAPTER", "native").strip().lower()
    if adapter_name == "native":
        return NativeSystemAdapter()
    source_path = os.getenv("LIARA_HEARTBEAT_SOURCE_PATH", "").strip()
    if not source_path:
        raise RuntimeError(f"LIARA_HEARTBEAT_SOURCE_PATH is required for adapter {adapter_name!r}")
    if adapter_name == "json":
        return JsonObservationAdapter(source_path)
    if adapter_name == "csv":
        mapping_path = os.getenv("LIARA_HEARTBEAT_CSV_MAPPING_PATH", "").strip()
        if not mapping_path:
            raise RuntimeError("LIARA_HEARTBEAT_CSV_MAPPING_PATH is required for CSV adapter")
        with open(mapping_path, "r", encoding="utf-8") as handle:
            mappings = json.load(handle)
        if not isinstance(mappings, list):
            raise RuntimeError("heartbeat CSV mapping must be a JSON list")
        return MappedCsvAdapter(source_path, mappings=mappings)
    raise RuntimeError(f"unsupported heartbeat adapter {adapter_name!r}")


def create_heartbeat_app(
    instance: HeartbeatInstance | None = None,
    *,
    adapter: ObservationAdapter | None = None,
    enable_collector: bool | None = None,
) -> FastAPI:
    instance = instance or HeartbeatInstance(
        instance_id=os.getenv("LIARA_HEARTBEAT_INSTANCE_ID", "liara.instance.heartbeat.local"),
        node_id=os.getenv("LIARA_HEARTBEAT_NODE_ID", "liara-local"),
        history_seconds=int(os.getenv("LIARA_HEARTBEAT_HISTORY_SECONDS", "900")),
        stale_seconds=int(os.getenv("LIARA_HEARTBEAT_STALE_SECONDS", "15")),
    )
    adapter = adapter or _configured_adapter()
    interval = max(0.5, float(os.getenv("LIARA_HEARTBEAT_INTERVAL_SECONDS", "2")))
    if enable_collector is None:
        enable_collector = os.getenv("LIARA_HEARTBEAT_COLLECT", "true").lower() in {"1", "true", "yes"}
    collector_error: str | None = None

    async def collect_loop() -> None:
        nonlocal collector_error
        while True:
            try:
                batch = await asyncio.to_thread(adapter.collect, node_id=instance.node_id)
                instance.ingest(batch)
                collector_error = None
            except Exception as exc:  # service remains observable when a sensor source fails
                collector_error = str(exc)
            await asyncio.sleep(interval)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(collect_loop()) if enable_collector else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    app = FastAPI(title="liara-heartbeat", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        snapshot = instance.snapshot()
        return {
            "status": "ok" if snapshot.state not in {"critical", "unknown"} else "degraded",
            "service": "liara-heartbeat",
            "instance_id": instance.instance_id,
            "node_id": instance.node_id,
            "collector": type(adapter).__name__,
            "collector_error": collector_error,
            "sequence": snapshot.sequence,
            "state": snapshot.state,
        }

    @app.get("/v1/heartbeat", response_model=HeartbeatSnapshot)
    async def heartbeat(response: Response) -> HeartbeatSnapshot:
        response.headers["Cache-Control"] = "no-store"
        return instance.snapshot()

    @app.get("/v1/curve", response_model=StateCurve)
    async def curve(
        response: Response,
        window_seconds: int = Query(default=300, ge=10, le=900),
    ) -> StateCurve:
        response.headers["Cache-Control"] = "no-store"
        return instance.curve(window_seconds=window_seconds)

    @app.get("/v1/status.txt", response_class=Response)
    async def human_status() -> Response:
        curve = instance.curve()
        lines = [
            f"{curve.instance_id}  {curve.state.upper()}  trend={curve.trend}",
            f"node={curve.node_id} confidence={curve.confidence:.2f} stability={curve.stability:.2f}",
        ]
        for metric in curve.metrics:
            lines.append(
                f"{metric.resource}/{metric.device_id}/{metric.metric}: "
                f"{metric.current:.3f} {metric.unit} slope={metric.slope_per_minute:+.3f}/min"
            )
        lines.append("signals=" + (", ".join(curve.signals) if curve.signals else "none"))
        return Response("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.post("/v1/observations", response_model=HeartbeatSnapshot)
    async def ingest(batch: ObservationBatch, authorization: str | None = Header(default=None)) -> HeartbeatSnapshot:
        allowed = os.getenv("LIARA_HEARTBEAT_ALLOW_EXTERNAL_INGEST", "false").lower() in {"1", "true", "yes"}
        token = os.getenv("LIARA_HEARTBEAT_INGEST_TOKEN", "")
        if not allowed:
            raise HTTPException(status_code=403, detail="external heartbeat ingestion is disabled")
        if not token or authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="invalid heartbeat ingestion token")
        try:
            return instance.ingest(batch)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    app.state.heartbeat_instance = instance
    return app


app = create_heartbeat_app()
