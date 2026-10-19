from __future__ import annotations

import gzip
import json
from pathlib import Path

from scripts.service_guard import SERVICES, ServiceGuard


def test_openvino_guard_uses_environment_preparing_launcher(tmp_path: Path):
    guard = ServiceGuard(tmp_path)

    command = guard._build_command(SERVICES["openvino_npu"], reload_mode=False)

    assert command[:6] == [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(tmp_path / "scripts" / "start_openvino_npu_instance.ps1"),
    ]
    assert "uvicorn" not in command


def test_openvino_is_part_of_guard_service_order():
    from scripts.service_guard import SERVICE_ORDER

    assert SERVICE_ORDER.index("embedding") < SERVICE_ORDER.index("openvino_npu")
    assert SERVICE_ORDER.index("openvino_npu") < SERVICE_ORDER.index("api")


def test_wait_until_stopped_requires_dead_pid_and_free_port(monkeypatch, tmp_path: Path):
    guard = ServiceGuard(tmp_path)
    service = SERVICES["openvino_npu"]
    pid_states = iter([True, False, False])
    port_states = iter([False, True])

    monkeypatch.setattr(guard, "_pid_running", lambda _pid: next(pid_states))
    monkeypatch.setattr(guard, "_port_bind_free", lambda _host, _port: next(port_states))
    monkeypatch.setattr("scripts.service_guard.time.sleep", lambda _seconds: None)

    assert guard._wait_until_stopped(service, 1234, timeout_seconds=1.0) is True


def test_audit_log_rotates_compressed_and_keeps_bounded_generations(tmp_path: Path):
    guard = ServiceGuard(tmp_path)
    guard.audit_rotate_bytes = 700
    guard.audit_backup_count = 2

    for sequence in range(18):
        guard.append_audit_event(
            command="start",
            service="embedding",
            result={"result": "healthy", "sequence": sequence, "detail": "x" * 100},
        )

    active_lines = guard.guard_audit_path.read_text(encoding="utf-8").splitlines()
    assert active_lines
    assert all(json.loads(line)["source"] == "service_guard" for line in active_lines)

    backups = sorted(guard.logs_dir.glob("service_guard.*.jsonl.gz"))
    assert 1 <= len(backups) <= 2
    for backup in backups:
        with gzip.open(backup, "rt", encoding="utf-8") as handle:
            archived_lines = handle.read().splitlines()
        assert archived_lines
        assert all(json.loads(line)["source"] == "service_guard" for line in archived_lines)


def test_audit_status_keeps_transitions_and_throttles_repeated_health(tmp_path: Path):
    guard = ServiceGuard(tmp_path)
    guard.audit_status_interval_seconds = 3600

    guard.append_audit_event(
        command="status", service="embedding", result={"result": "healthy"}
    )
    guard.append_audit_event(
        command="status", service="embedding", result={"result": "healthy"}
    )
    guard.append_audit_event(
        command="status", service="embedding", result={"result": "not-connectable"}
    )

    events = [
        json.loads(line)
        for line in guard.guard_audit_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["result"] for event in events] == ["healthy", "not-connectable"]
