from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.contracts import ObservationBatch, ResourceObservation
from services.heartbeat.adapters import MappedCsvAdapter
from services.heartbeat.app import create_heartbeat_app
from services.heartbeat.core import HeartbeatInstance


def _observation(
    resource: str,
    metric: str,
    value: float,
    unit: str,
    observed_at: datetime,
) -> ResourceObservation:
    return ResourceObservation(
        resource=resource,
        metric=metric,
        value=value,
        unit=unit,
        source_id="test-probe",
        observed_at=observed_at,
    )


def test_contract_rejects_source_specific_or_invalid_metric_values():
    with pytest.raises(ValidationError):
        ResourceObservation(
            resource="ram",
            metric="memory_used_ratio",
            value=79.0,
            unit="ratio",
            source_id="hwinfo",
        )

    with pytest.raises(ValidationError):
        ResourceObservation(
            resource="cpu",
            metric="hwinfo_total_cpu_usage",
            value=0.4,
            unit="ratio",
            source_id="hwinfo",
        )


def test_instance_derives_curve_and_scheduler_envelope_from_history():
    instance = HeartbeatInstance(node_id="node-a", stale_seconds=30)
    now = datetime.now(UTC)
    instance.ingest(ObservationBatch(node_id="node-a", observations=[
        _observation("cpu", "utilization_ratio", 0.2, "ratio", now - timedelta(seconds=60)),
        _observation("ram", "memory_used_ratio", 0.7, "ratio", now - timedelta(seconds=60)),
        _observation("thermal", "temperature_c", 70.0, "celsius", now - timedelta(seconds=60)),
    ]))
    instance.ingest(ObservationBatch(node_id="node-a", observations=[
        _observation("cpu", "utilization_ratio", 0.4, "ratio", now),
        _observation("ram", "memory_used_ratio", 0.8, "ratio", now),
        _observation("thermal", "temperature_c", 86.0, "celsius", now),
    ]))

    curve = instance.curve(window_seconds=60, now=now)

    cpu = next(metric for metric in curve.metrics if metric.resource == "cpu")
    assert cpu.sample_count == 2
    assert cpu.slope_per_minute == pytest.approx(0.2)
    assert curve.state == "constrained"
    assert curve.trend == "degrading"
    assert "thermal_pressure" in curve.signals
    assert curve.envelope.cpu_budget == pytest.approx(0.6)
    assert curve.envelope.ram_budget == pytest.approx(0.2)
    assert curve.envelope.capacity == pytest.approx(0.1)


def test_battery_relation_creates_combined_signal():
    instance = HeartbeatInstance(node_id="node-a")
    now = datetime.now(UTC)
    instance.ingest(ObservationBatch(node_id="node-a", observations=[
        _observation("battery", "charge_ratio", 0.18, "ratio", now),
        _observation("battery", "external_power_connected", 1.0, "boolean", now),
    ]))
    snapshot = instance.snapshot(now=now)
    assert "battery_low" in snapshot.signals
    assert "battery_low_while_connected" in snapshot.signals


def test_csv_adapter_contains_vendor_names_only_in_mapping(tmp_path):
    source = tmp_path / "sensors.csv"
    source.write_text("Total CPU Usage [%],Physical Memory Load [%]\n31,79\n", encoding="utf-8")
    adapter = MappedCsvAdapter(source, mappings=[
        {"column": "Total CPU Usage [%]", "resource": "cpu", "metric": "utilization_ratio", "unit": "ratio", "scale": "0.01"},
        {"column": "Physical Memory Load [%]", "resource": "ram", "metric": "memory_used_ratio", "unit": "ratio", "scale": "0.01"},
    ])
    batch = adapter.collect(node_id="node-a")
    payload = batch.model_dump_json()
    assert batch.observations[0].value == pytest.approx(0.31)
    assert "Total CPU Usage" not in payload
    assert "Physical Memory Load" not in payload


def test_api_separates_read_surface_from_protected_ingestion(monkeypatch):
    monkeypatch.delenv("LIARA_HEARTBEAT_ALLOW_EXTERNAL_INGEST", raising=False)
    instance = HeartbeatInstance(node_id="node-a")
    now = datetime.now(UTC)
    instance.ingest(ObservationBatch(node_id="node-a", observations=[
        _observation("cpu", "utilization_ratio", 0.25, "ratio", now),
    ]))
    app = create_heartbeat_app(instance, enable_collector=False)
    with TestClient(app) as client:
        heartbeat = client.get("/v1/heartbeat")
        curve = client.get("/v1/curve?window_seconds=60")
        human = client.get("/v1/status.txt")
        denied = client.post("/v1/observations", json={
            "node_id": "node-a",
            "observations": [{
                "resource": "cpu", "metric": "utilization_ratio", "value": 0.5,
                "unit": "ratio", "source_id": "external", "observed_at": now.isoformat(),
            }],
        })
    assert heartbeat.status_code == 200
    assert heartbeat.headers["cache-control"] == "no-store"
    assert curve.json()["instance_id"] == "liara.instance.heartbeat.local"
    assert "CPU/default/utilization_ratio".lower() in human.text.lower()
    assert denied.status_code == 403


def test_instance_rejects_observations_for_another_node():
    instance = HeartbeatInstance(node_id="node-a")
    with pytest.raises(ValueError, match="does not match"):
        instance.ingest(ObservationBatch(node_id="node-b", observations=[
            ResourceObservation(
                resource="cpu", metric="utilization_ratio", value=0.2,
                unit="ratio", source_id="test",
            )
        ]))
