"""State history and curve derivation for the LIARA heartbeat instance."""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from services.contracts.heartbeat import (
    HeartbeatSnapshot,
    HeartbeatState,
    HeartbeatTrend,
    MetricCurve,
    MetricPoint,
    ObservationBatch,
    ResourceEnvelope,
    ResourceObservation,
    StateCurve,
)


class HeartbeatInstance:
    """Owns normalized observations; it never schedules or executes work."""

    def __init__(
        self,
        *,
        instance_id: str = "liara.instance.heartbeat.local",
        node_id: str = "liara-local",
        history_seconds: int = 900,
        stale_seconds: int = 15,
    ) -> None:
        self.instance_id = instance_id
        self.node_id = node_id
        self.history_seconds = max(60, int(history_seconds))
        self.stale_seconds = max(2, int(stale_seconds))
        self._samples: dict[str, deque[ResourceObservation]] = defaultdict(deque)
        self._sequence = 0

    def ingest(self, batch: ObservationBatch) -> HeartbeatSnapshot:
        if batch.node_id != self.node_id:
            raise ValueError(f"batch node_id {batch.node_id!r} does not match {self.node_id!r}")
        now = datetime.now(UTC)
        oldest = now - timedelta(seconds=self.history_seconds)
        for observation in batch.observations:
            bucket = self._samples[observation.key]
            bucket.append(observation)
            while bucket and bucket[0].observed_at < oldest:
                bucket.popleft()
        self._sequence += 1
        return self.snapshot(now=now)

    def snapshot(self, *, now: datetime | None = None) -> HeartbeatSnapshot:
        now = now or datetime.now(UTC)
        latest = sorted(
            (bucket[-1] for bucket in self._samples.values() if bucket),
            key=lambda item: item.key,
        )
        signals, state = self._evaluate(latest, now)
        confidence = self._confidence(latest, now)
        return HeartbeatSnapshot(
            instance_id=self.instance_id,
            node_id=self.node_id,
            sequence=self._sequence,
            observed_at=now,
            state=state,
            observations=latest,
            signals=signals,
            confidence=confidence,
        )

    def curve(self, *, window_seconds: int = 300, now: datetime | None = None) -> StateCurve:
        now = now or datetime.now(UTC)
        window_seconds = max(10, min(int(window_seconds), self.history_seconds))
        cutoff = now - timedelta(seconds=window_seconds)
        curves: list[MetricCurve] = []
        for key, bucket in sorted(self._samples.items()):
            samples = [sample for sample in bucket if sample.observed_at >= cutoff]
            if not samples:
                continue
            values = [sample.value for sample in samples]
            first, last = samples[0], samples[-1]
            elapsed_minutes = max((last.observed_at - first.observed_at).total_seconds() / 60.0, 0.0)
            slope = (last.value - first.value) / elapsed_minutes if elapsed_minutes > 0 else 0.0
            curves.append(
                MetricCurve(
                    key=key,
                    resource=last.resource,
                    metric=last.metric,
                    unit=last.unit,
                    device_id=last.device_id,
                    current=last.value,
                    minimum=min(values),
                    maximum=max(values),
                    mean=sum(values) / len(values),
                    slope_per_minute=slope,
                    sample_count=len(samples),
                    duration_seconds=max((last.observed_at - first.observed_at).total_seconds(), 0.0),
                    confidence=sum(sample.confidence for sample in samples) / len(samples),
                    last_observed_at=last.observed_at,
                    points=[MetricPoint(observed_at=sample.observed_at, value=sample.value) for sample in samples[-450:]],
                )
            )
        latest = [bucket[-1] for bucket in self._samples.values() if bucket]
        signals, state = self._evaluate(latest, now)
        trend = self._trend(curves, signals)
        stability = self._stability(curves)
        return StateCurve(
            instance_id=self.instance_id,
            node_id=self.node_id,
            generated_at=now,
            window_seconds=window_seconds,
            state=state,
            trend=trend,
            stability=stability,
            confidence=self._confidence(latest, now),
            metrics=curves,
            signals=signals,
            envelope=self._envelope(curves, state),
        )

    def _evaluate(
        self, observations: list[ResourceObservation], now: datetime
    ) -> tuple[list[str], HeartbeatState]:
        if not observations:
            return ["no_observations"], "unknown"
        signals: list[str] = []
        if all((now - item.observed_at).total_seconds() > self.stale_seconds for item in observations):
            return ["heartbeat_stale"], "unknown"
        values_by_key = {(item.resource, item.metric): item.value for item in observations}
        for item in observations:
            if item.metric == "temperature_c":
                prefix = "thermal" if item.resource == "thermal" else f"{item.resource}_thermal"
                if item.value >= 95:
                    signals.append(f"{prefix}_critical")
                elif item.value >= 85:
                    signals.append(f"{prefix}_pressure")
            elif item.metric == "memory_used_ratio" and item.value >= 0.9:
                signals.append(f"{item.resource}_memory_pressure")
            elif item.resource == "battery" and item.metric == "charge_ratio" and item.value <= 0.2:
                signals.append("battery_low")
            elif item.metric == "available" and item.value == 0:
                signals.append(f"{item.resource}_unavailable")
        if (
            values_by_key.get(("battery", "charge_ratio"), 1.0) <= 0.2
            and values_by_key.get(("battery", "external_power_connected"), 0.0) == 1.0
        ):
            signals.append("battery_low_while_connected")
        signals = sorted(set(signals))
        if any("critical" in signal for signal in signals):
            state: HeartbeatState = "critical"
        elif any("unavailable" in signal for signal in signals):
            state = "degraded"
        elif signals:
            state = "constrained"
        else:
            state = "healthy"
        return signals, state

    def _confidence(self, observations: list[ResourceObservation], now: datetime) -> float:
        if not observations:
            return 0.0
        weighted = []
        for item in observations:
            age = max(0.0, (now - item.observed_at).total_seconds())
            freshness = max(0.0, 1.0 - age / self.stale_seconds)
            weighted.append(item.confidence * freshness)
        return max(0.0, min(1.0, sum(weighted) / len(weighted)))

    @staticmethod
    def _trend(curves: list[MetricCurve], signals: list[str]) -> HeartbeatTrend:
        pressure_slopes = []
        for curve in curves:
            if curve.sample_count < 2 or curve.duration_seconds < 30.0:
                continue
            if curve.metric in {"utilization_ratio", "memory_used_ratio", "temperature_c"}:
                scale = 100.0 if curve.unit == "ratio" else 1.0
                pressure_slopes.append(curve.slope_per_minute * scale)
            elif curve.resource == "battery" and curve.metric == "charge_ratio":
                pressure_slopes.append(-curve.slope_per_minute * 100.0)
        if not pressure_slopes:
            return "unknown"
        score = sum(pressure_slopes) / len(pressure_slopes)
        if score > 1.0 or signals and score > 0.2:
            return "degrading"
        if score < -1.0:
            return "improving"
        return "stable"

    @staticmethod
    def _stability(curves: list[MetricCurve]) -> float:
        if not curves:
            return 0.0
        normalized_ranges = []
        for curve in curves:
            scale = 1.0 if curve.unit in {"ratio", "boolean"} else max(abs(curve.mean), 1.0)
            normalized_ranges.append(min(1.0, abs(curve.maximum - curve.minimum) / scale))
        return max(0.0, min(1.0, 1.0 - sum(normalized_ranges) / len(normalized_ranges)))

    @staticmethod
    def _envelope(curves: list[MetricCurve], state: HeartbeatState) -> ResourceEnvelope:
        budgets: dict[str, float] = {}
        for curve in curves:
            if curve.metric == "utilization_ratio" and curve.resource in {"cpu", "gpu", "npu"}:
                budgets[curve.resource] = max(0.0, min(1.0, 1.0 - curve.current))
            elif curve.resource == "ram" and curve.metric == "memory_used_ratio":
                budgets["ram"] = max(0.0, min(1.0, 1.0 - curve.current))
        known = list(budgets.values())
        capacity = min(known) if known else 0.0
        if state == "critical":
            capacity = 0.0
        elif state == "degraded":
            capacity *= 0.25
        elif state == "constrained":
            capacity *= 0.5
        return ResourceEnvelope(
            capacity=capacity,
            cpu_budget=budgets.get("cpu"),
            ram_budget=budgets.get("ram"),
            gpu_budget=budgets.get("gpu"),
            npu_budget=budgets.get("npu"),
            max_parallel_jobs=0 if capacity <= 0.05 else (1 if capacity < 0.5 else 2),
        )
