from __future__ import annotations

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