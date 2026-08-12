"""FastAPI surface for LIARA's independent cyclic self-observer."""

from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Query, Response

from services.contracts.self_observer import (
    SelfInspectionCanaryRequest,
    SelfInspectionDecision,
    SystemStateEnvelope,
)
from .assurance import HttpValidatorSubmitter, SelfInspectionGate
from .core import SelfObserverInstance
from .probes import SelfObserverProbes


def create_self_observer_app(
    instance: SelfObserverInstance | None = None,
    *,
    probes: SelfObserverProbes | None = None,
    inspection_gate: SelfInspectionGate | None = None,
    enable_collector: bool | None = None,
) -> FastAPI:
    instance = instance or SelfObserverInstance(
        observer_id=os.getenv("LIARA_SELF_OBSERVER_INSTANCE_ID", "liara.instance.self-observer.local"),
        node_id=os.getenv("LIARA_SELF_OBSERVER_NODE_ID", "liara-local"),
        store_dir=os.getenv("LIARA_SELF_OBSERVER_STORE_DIR", "data/self_observer"),
        history_limit=int(os.getenv("LIARA_SELF_OBSERVER_HISTORY_LIMIT", "240")),
        quiet_candidate_cycles=int(os.getenv("LIARA_SELF_OBSERVER_QUIET_CANDIDATE_CYCLES", "2")),
        quiet_stable_cycles=int(os.getenv("LIARA_SELF_OBSERVER_QUIET_STABLE_CYCLES", "4")),
    )
    probes = probes or SelfObserverProbes(
        api_base_url=os.getenv("LIARA_SELF_OBSERVER_API_BASE_URL", "http://127.0.0.1:8010"),
        memory_base_url=os.getenv("LIARA_MEMORY_SERVICE_URL", "http://127.0.0.1:8020"),
        heartbeat_base_url=os.getenv("LIARA_SELF_OBSERVER_HEARTBEAT_BASE_URL", "http://127.0.0.1:8050"),
        timeout_seconds=float(os.getenv("LIARA_SELF_OBSERVER_TIMEOUT_SECONDS", "4")),
        backend_timeout_seconds=float(os.getenv("LIARA_SELF_OBSERVER_BACKEND_TIMEOUT_SECONDS", "12")),
        assurance_stale_seconds=int(os.getenv("LIARA_SELF_OBSERVER_ASSURANCE_STALE_SECONDS", "86400")),
    )
    inspection_mode = os.getenv("LIARA_SELF_INSPECTION_MODE", "observe").strip().lower()
    if inspection_gate is None:
        submitter = None
        if inspection_mode in {"observe", "submit"}:
            submitter = HttpValidatorSubmitter(
                memory_base_url=os.getenv("LIARA_MEMORY_SERVICE_URL", "http://127.0.0.1:8020"),
                timeout_seconds=float(os.getenv("LIARA_SELF_INSPECTION_SUBMIT_TIMEOUT_SECONDS", "30")),
            )
        inspection_gate = SelfInspectionGate(
            mode=inspection_mode,
            workspace=os.getenv("LIARA_SELF_INSPECTION_WORKSPACE"),
            scope=os.getenv("LIARA_SELF_INSPECTION_SCOPE", "quick"),
            strict_mode=os.getenv("LIARA_SELF_INSPECTION_STRICT", "false").lower() in {"1", "true", "yes"},
            minimum_interval_seconds=int(os.getenv("LIARA_SELF_INSPECTION_MIN_INTERVAL_SECONDS", "21600")),
            evidence_stale_seconds=int(os.getenv("LIARA_SELF_OBSERVER_ASSURANCE_STALE_SECONDS", "86400")),
            store_dir=instance.store_dir,
            submitter=submitter,
            node_id=instance.node_id,
        )
    interval = max(2.0, float(os.getenv("LIARA_SELF_OBSERVER_INTERVAL_SECONDS", "30")))
    tracking_interval = max(0.5, float(os.getenv("LIARA_SELF_INSPECTION_TRACK_INTERVAL_SECONDS", "1")))
    canary_enabled = os.getenv("LIARA_SELF_INSPECTION_CANARY_ENABLED", "false").lower() in {"1", "true", "yes"}
    canary_token = os.getenv("LIARA_SELF_INSPECTION_CANARY_TOKEN", "")
    canary_consumed = False
    if enable_collector is None:
        enable_collector = os.getenv("LIARA_SELF_OBSERVER_COLLECT", "true").lower() in {"1", "true", "yes"}
    collector_error: str | None = None
    gate_lock = asyncio.Lock()

    async def collect_once() -> SystemStateEnvelope:
        nonlocal collector_error
        try:
            evidence = await probes.collect()
            async with gate_lock:
                await inspection_gate.refresh()
                tracked_assurance = inspection_gate.assurance_evidence()
                if tracked_assurance is not None:
                    evidence = [item for item in evidence if item.domain != "assurance"] + [tracked_assurance]
                envelope = instance.observe(evidence)
                await inspection_gate.evaluate(envelope)
            collector_error = None
            return envelope
        except Exception as exc:
            collector_error = f"{type(exc).__name__}: {exc}"
            raise

    async def collect_loop() -> None:
        while True:
            try:
                await collect_once()
            except Exception:
                pass
            await asyncio.sleep(interval)

    async def inspection_track_loop() -> None:
        while True:
            async with gate_lock:
                await inspection_gate.refresh()
            await asyncio.sleep(tracking_interval)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(collect_loop()) if enable_collector else None
        tracking_task = asyncio.create_task(inspection_track_loop()) if enable_collector else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
            if tracking_task is not None:
                tracking_task.cancel()
            await asyncio.gather(*(item for item in (task, tracking_task) if item is not None), return_exceptions=True)

    app = FastAPI(title="liara-self-observer", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict:
        latest = instance.latest()
        return {
            "status": "ok" if collector_error is None else "degraded",
            "service": "liara-self-observer",
            "observer_id": instance.observer_id,
            "node_id": instance.node_id,
            "sequence": latest.sequence if latest else 0,
            "state": latest.state if latest else "unknown",
            "phase": latest.phase if latest else "observing",
            "collector_error": collector_error,
            "inspection_mode": inspection_gate.mode,
            "inspection_action": inspection_gate.latest().action if inspection_gate.latest() else "none",
        }

    @app.get("/v1/state", response_model=SystemStateEnvelope)
    async def state(response: Response) -> SystemStateEnvelope:
        response.headers["Cache-Control"] = "no-store"
        latest = instance.latest()
        if latest is None:
            latest = await collect_once()
        return latest

    @app.get("/v1/history", response_model=list[SystemStateEnvelope])
    async def history(response: Response, limit: int = Query(default=60, ge=1, le=240)) -> list[SystemStateEnvelope]:
        response.headers["Cache-Control"] = "no-store"
        return instance.history(limit)

    @app.get("/v1/inspection", response_model=SelfInspectionDecision)
    async def inspection(response: Response) -> SelfInspectionDecision:
        response.headers["Cache-Control"] = "no-store"
        decision = inspection_gate.latest()
        if decision is None:
            latest = instance.latest() or await collect_once()
            decision = inspection_gate.latest() or await inspection_gate.evaluate(latest)
        return decision

    @app.post("/v1/inspection/canary", response_model=SelfInspectionDecision)
    async def inspection_canary(
        request: SelfInspectionCanaryRequest,
        response: Response,
        authorization: str | None = Header(default=None),
    ) -> SelfInspectionDecision:
        nonlocal canary_consumed
        response.headers["Cache-Control"] = "no-store"
        supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
        if canary_consumed or not canary_enabled or not canary_token or not secrets.compare_digest(supplied, canary_token):
            raise HTTPException(status_code=403, detail="self_inspection_canary_not_authorized")
        latest = instance.latest() or await collect_once()
        async with gate_lock:
            decision = await inspection_gate.submit_canary(
                latest,
                authorization_id=request.authorization_id,
                reason=request.reason,
            )
            if decision.action == "submitted":
                canary_consumed = True
            return decision

    @app.get("/v1/status.txt", response_class=Response)
    async def human_status() -> Response:
        latest = instance.latest() or await collect_once()
        lines = [
            f"{latest.observer_id}  {latest.state.upper()}  phase={latest.phase} trend={latest.trend}",
            f"sequence={latest.sequence} confidence={latest.confidence:.2f} stability={latest.stability:.2f}",
            "sources=" + ", ".join(f"{item.domain}:{item.state}" for item in latest.evidence),
            "signals=" + (", ".join(latest.signals) if latest.signals else "none"),
        ]
        decision = inspection_gate.latest()
        if decision is not None:
            lines.append(
                f"inspection={decision.mode}:{decision.action} eligible={str(decision.eligible).lower()} "
                f"reasons={','.join(decision.reasons) if decision.reasons else 'none'}"
            )
        return Response("\n".join(lines) + "\n", media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})

    app.state.self_observer = instance
    app.state.collect_once = collect_once
    app.state.inspection_gate = inspection_gate
    return app


app = create_self_observer_app()
