"""Adapters from concrete runtime endpoints to normalized state evidence."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import httpx

from services.contracts.self_observer import StateEvidence


def _timestamp(value: object, *, fallback: datetime) -> datetime:
    if not isinstance(value, str):
        return fallback
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


class SelfObserverProbes:
    """Reads existing boundaries; it has no mutation or execution methods."""

    def __init__(
        self,
        *,
        api_base_url: str = "http://127.0.0.1:8010",
        memory_base_url: str = "http://127.0.0.1:8020",
        heartbeat_base_url: str = "http://127.0.0.1:8050",
        timeout_seconds: float = 4.0,
        backend_timeout_seconds: float = 12.0,
        assurance_stale_seconds: int = 86_400,
    ) -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.memory_base_url = memory_base_url.rstrip("/")
        self.heartbeat_base_url = heartbeat_base_url.rstrip("/")
        self.timeout_seconds = max(0.5, timeout_seconds)
        self.backend_timeout_seconds = max(self.timeout_seconds, backend_timeout_seconds)
        self.assurance_stale_seconds = max(60, assurance_stale_seconds)

    async def collect(self) -> list[StateEvidence]:
        return list(await asyncio.gather(
            self.hardware(),
            self.software(),
            self.assurance(),
        ))

    async def hardware(self) -> StateEvidence:
        now = datetime.now(UTC)
        try:
            async with httpx.AsyncClient(base_url=self.heartbeat_base_url, timeout=self.timeout_seconds) as client:
                response = await client.get("/v1/curve", params={"window_seconds": 300})
                response.raise_for_status()
                payload = response.json()
            state = {
                "healthy": "healthy", "constrained": "attention", "degraded": "degraded",
                "critical": "critical", "unknown": "unknown",
            }.get(str(payload.get("state")), "unknown")
            envelope = payload.get("envelope") if isinstance(payload.get("envelope"), dict) else {}
            return StateEvidence(
                domain="hardware",
                source_id="liara-heartbeat",
                observed_at=_timestamp(payload.get("generated_at"), fallback=now),
                state=state,
                confidence=float(payload.get("confidence", 0.0)),
                stability=float(payload.get("stability", 0.0)),
                signals=[str(item) for item in payload.get("signals", [])],
                attributes={
                    "capacity": float(envelope.get("capacity", 0.0)),
                    "max_parallel_jobs": int(envelope.get("max_parallel_jobs", 0)),
                },
            )
        except Exception as exc:
            return self._unreachable("hardware", "liara-heartbeat", exc, now)

    async def software(self) -> StateEvidence:
        now = datetime.now(UTC)
        try:
            async with (
                httpx.AsyncClient(base_url=self.api_base_url, timeout=self.timeout_seconds) as api_client,
                httpx.AsyncClient(base_url=self.memory_base_url, timeout=self.backend_timeout_seconds) as memory_client,
            ):
                health_response, backends_response = await asyncio.gather(
                    api_client.get("/health"),
                    memory_client.get("/health/backends"),
                )
            health_response.raise_for_status()
            backends_response.raise_for_status()
            health = health_response.json()
            backends = backends_response.json()
            backend_health = backends.get("backend_health") if isinstance(backends.get("backend_health"), dict) else {}
            total = len(backend_health)
            healthy = sum(1 for value in backend_health.values() if value == "healthy")
            signals = [f"backend_{name}_{status}" for name, status in backend_health.items() if status != "healthy"]
            api_healthy = health.get("status") == "ok"
            state = "healthy" if api_healthy and healthy == total else "degraded"
            confidence = 1.0 if total > 0 else 0.7
            return StateEvidence(
                domain="software",
                source_id="liara-api-health",
                observed_at=now,
                state=state,
                confidence=confidence,
                stability=1.0 if state == "healthy" else 0.5,
                signals=signals,
                attributes={"api_healthy": api_healthy, "backends_healthy": healthy, "backends_total": total},
            )
        except Exception as exc:
            return self._unreachable("software", "liara-api-health", exc, now)

    async def assurance(self) -> StateEvidence:
        now = datetime.now(UTC)
        try:
            async with httpx.AsyncClient(base_url=self.api_base_url, timeout=self.timeout_seconds) as client:
                response = await client.get(
                    "/operations/workspace",
                    params={"artifact_type": "validation", "limit": 1},
                )
                response.raise_for_status()
                payload = response.json()
            artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
            if not artifacts:
                return StateEvidence(
                    domain="assurance", source_id="ai-validator-artifacts", observed_at=now,
                    state="unknown", confidence=0.25, stability=0.5,
                    signals=["no_validator_evidence"], attributes={"findings_count": None},
                )
            artifact: dict[str, Any] = artifacts[0]
            summary = artifact.get("summary") if isinstance(artifact.get("summary"), dict) else {}
            findings = int(summary.get("findings_count", 0))
            observed_at = _timestamp(artifact.get("timestamp"), fallback=now)
            age = max(0.0, (now - observed_at).total_seconds())
            stale = age > self.assurance_stale_seconds
            state = "attention" if findings > 0 or stale else "healthy"
            signals = []
            if findings > 0:
                signals.append("validator_findings")
            if stale:
                signals.append("validator_evidence_stale")
            return StateEvidence(
                domain="assurance", source_id="ai-validator-artifacts", observed_at=observed_at,
                state=state, confidence=0.6 if stale else 0.95, stability=1.0,
                signals=signals,
                attributes={
                    "findings_count": findings,
                    "job_id": str(summary.get("job_id", "unknown")),
                    "age_seconds": round(age, 3),
                },
            )
        except Exception as exc:
            return self._unreachable("assurance", "ai-validator-artifacts", exc, now)

    @staticmethod
    def _unreachable(domain: str, source_id: str, exc: Exception, now: datetime) -> StateEvidence:
        return StateEvidence(
            domain=domain,  # type: ignore[arg-type]
            source_id=source_id,
            observed_at=now,
            state="degraded",
            confidence=0.2,
            stability=0.0,
            signals=[f"{domain}_source_unreachable"],
            attributes={"error_type": type(exc).__name__},
        )
