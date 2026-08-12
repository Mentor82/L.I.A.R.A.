"""Source adapters that emit only the canonical heartbeat observation model."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from services.contracts.heartbeat import ObservationBatch, ResourceObservation


class ObservationAdapter(Protocol):
    source_id: str

    def collect(self, *, node_id: str) -> ObservationBatch: ...


class NativeSystemAdapter:
    """Portable direct reader. Optional psutil expands available measurements."""

    source_id = "native-system"

    def collect(self, *, node_id: str) -> ObservationBatch:
        try:
            import psutil  # type: ignore
        except ImportError as exc:
            raise RuntimeError("native adapter requires optional dependency psutil") from exc

        now = datetime.now(UTC)
        observations = [
            ResourceObservation(
                resource="cpu", metric="utilization_ratio", value=psutil.cpu_percent(interval=0.1) / 100.0,
                unit="ratio", source_id=self.source_id, observed_at=now,
            ),
            ResourceObservation(
                resource="ram", metric="memory_used_ratio", value=psutil.virtual_memory().percent / 100.0,
                unit="ratio", source_id=self.source_id, observed_at=now,
            ),
        ]
        battery = psutil.sensors_battery()
        if battery is not None:
            observations.extend([
                ResourceObservation(
                    resource="battery", metric="charge_ratio", value=battery.percent / 100.0,
                    unit="ratio", source_id=self.source_id, observed_at=now,
                ),
                ResourceObservation(
                    resource="battery", metric="external_power_connected", value=1.0 if battery.power_plugged else 0.0,
                    unit="boolean", source_id=self.source_id, observed_at=now,
                ),
            ])
        try:
            temperature_groups = psutil.sensors_temperatures() or {}
        except (AttributeError, OSError):
            temperature_groups = {}
        for group, entries in temperature_groups.items():
            for index, entry in enumerate(entries):
                if entry.current is None:
                    continue
                observations.append(ResourceObservation(
                    resource="thermal", metric="temperature_c", value=float(entry.current), unit="celsius",
                    device_id=f"{group}:{index}", source_id=self.source_id, observed_at=now,
                    attributes={"label": entry.label or group},
                ))
        return ObservationBatch(node_id=node_id, observations=observations)


class JsonObservationAdapter:
    """Reads the canonical interchange contract from a file or pipe output."""

    source_id = "canonical-json"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def collect(self, *, node_id: str) -> ObservationBatch:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload.setdefault("node_id", node_id)
        return ObservationBatch.model_validate(payload)


class MappedCsvAdapter:
    """Maps any CSV exporter (including HWiNFO logs) into canonical metrics.

    Source column names live only in configuration and never escape this adapter.
    """

    source_id = "mapped-csv"

    def __init__(self, path: str | Path, mappings: list[dict[str, str]]) -> None:
        self.path = Path(path)
        self.mappings = mappings

    def collect(self, *, node_id: str) -> ObservationBatch:
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise RuntimeError(f"CSV source {self.path} contains no data rows")
        row = rows[-1]
        now = datetime.now(UTC)
        observations = []
        for mapping in self.mappings:
            raw = row[mapping["column"]].strip().replace(",", ".")
            scale = float(mapping.get("scale", "1"))
            observations.append(ResourceObservation(
                resource=mapping["resource"], metric=mapping["metric"],
                value=float(raw) * scale, unit=mapping["unit"],
                device_id=mapping.get("device_id", "default"), source_id=self.source_id,
                observed_at=now,
            ))
        return ObservationBatch(node_id=node_id, observations=observations)
