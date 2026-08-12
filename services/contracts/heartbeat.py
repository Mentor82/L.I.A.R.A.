"""Vendor-neutral contracts for LIARA runtime heartbeat instances."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


ResourceKind = Literal["cpu", "ram", "gpu", "npu", "battery", "thermal", "power", "system"]
MetricName = Literal[
    "utilization_ratio",
    "memory_used_ratio",
    "temperature_c",
    "power_w",
    "charge_ratio",
    "charge_rate_w",
    "external_power_connected",
    "queue_depth",
    "active_work",
    "available",
]
MetricUnit = Literal["ratio", "celsius", "watts", "count", "boolean"]
HeartbeatState = Literal["healthy", "constrained", "degraded", "critical", "unknown"]
HeartbeatTrend = Literal["improving", "stable", "degrading", "unknown"]


_EXPECTED_UNITS: dict[str, str] = {
    "utilization_ratio": "ratio",
    "memory_used_ratio": "ratio",
    "temperature_c": "celsius",
    "power_w": "watts",
    "charge_ratio": "ratio",
    "charge_rate_w": "watts",
    "external_power_connected": "boolean",
    "queue_depth": "count",
    "active_work": "count",
    "available": "boolean",
}


class ResourceObservation(BaseModel):
    """One normalized observation, independent of its sensor implementation."""

    resource: ResourceKind
    metric: MetricName
    value: float
    unit: MetricUnit
    device_id: str = Field(default="default", min_length=1, max_length=128)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_id: str = Field(min_length=1, max_length=128)
    confidence: Annotated[float, Field(ge=0.0, le=1.0)] = 1.0
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_semantics(self) -> "ResourceObservation":
        expected = _EXPECTED_UNITS[self.metric]
        if self.unit != expected:
            raise ValueError(f"metric {self.metric!r} requires unit {expected!r}")
        if self.unit == "ratio" and not 0.0 <= self.value <= 1.0:
            raise ValueError("ratio values must be between 0 and 1")
        if self.unit == "boolean" and self.value not in (0.0, 1.0):
            raise ValueError("boolean values must be 0 or 1")
        if self.unit == "count" and self.value < 0:
            raise ValueError("count values must be non-negative")
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must contain timezone information")
        return self

    @property
    def key(self) -> str:
        return f"{self.resource}:{self.device_id}:{self.metric}"


class ObservationBatch(BaseModel):
    """Canonical adapter boundary accepted by a heartbeat instance."""

    schema_version: Literal["1.0"] = "1.0"
    node_id: str = Field(min_length=1, max_length=128)
    observations: list[ResourceObservation] = Field(min_length=1, max_length=256)


class MetricPoint(BaseModel):
    observed_at: datetime
    value: float


class MetricCurve(BaseModel):
    key: str
    resource: ResourceKind
    metric: MetricName
    unit: MetricUnit
    device_id: str
    current: float
    minimum: float
    maximum: float
    mean: float
    slope_per_minute: float
    sample_count: int = Field(ge=1)
    duration_seconds: float = Field(ge=0.0)
    confidence: float = Field(ge=0.0, le=1.0)
    last_observed_at: datetime
    points: list[MetricPoint] = Field(default_factory=list, max_length=450)


class ResourceEnvelope(BaseModel):
    capacity: float = Field(ge=0.0, le=1.0)
    cpu_budget: float | None = Field(default=None, ge=0.0, le=1.0)
    ram_budget: float | None = Field(default=None, ge=0.0, le=1.0)
    gpu_budget: float | None = Field(default=None, ge=0.0, le=1.0)
    npu_budget: float | None = Field(default=None, ge=0.0, le=1.0)
    max_parallel_jobs: int = Field(default=1, ge=0)


class HeartbeatSnapshot(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    instance_id: str
    instance_type: Literal["heartbeat"] = "heartbeat"
    node_id: str
    sequence: int = Field(ge=0)
    observed_at: datetime
    state: HeartbeatState
    observations: list[ResourceObservation]
    signals: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class StateCurve(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    instance_id: str
    node_id: str
    generated_at: datetime
    window_seconds: int = Field(ge=1)
    state: HeartbeatState
    trend: HeartbeatTrend
    stability: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    metrics: list[MetricCurve]
    signals: list[str] = Field(default_factory=list)
    envelope: ResourceEnvelope


class HeartbeatOperationsResponse(BaseModel):
    """Read-only API projection used by operations and architecture UIs."""

    status: Literal["success", "failed"]
    service_health: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    heartbeat: HeartbeatSnapshot | None = None
    curve: StateCurve | None = None
    error: str | None = None
