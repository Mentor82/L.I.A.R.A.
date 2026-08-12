from __future__ import annotations

from pathlib import Path

import server_management_gui as manager


class _FakeProcess:
    def __init__(self, lines: list[str], exit_code: int) -> None:
        self.stdout = iter(lines)
        self.exit_code = exit_code

    def wait(self) -> int:
        return self.exit_code


class _RunningProcess:
    pid = 1234

    @staticmethod
    def poll():
        return None


def test_frontend_build_streams_output_and_reports_restart(monkeypatch, tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}
    emitted: list[str] = []

    monkeypatch.setattr(manager, "_resolve_npm_executable", lambda: "C:/tools/npm.cmd")

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return _FakeProcess(["building\n", "done\n"], 0)

    monkeypatch.setattr(manager.subprocess, "Popen", fake_popen)

    ok, message = manager.run_frontend_build(str(tmp_path), emitted.append)

    assert ok is True
    assert "restart" in message.lower()
    assert captured["command"] == ["C:/tools/npm.cmd", "run", "build"]
    assert captured["cwd"] == str(tmp_path)
    assert emitted == ["building", "done"]


def test_frontend_build_reports_missing_npm(monkeypatch, tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(manager, "_resolve_npm_executable", lambda: None)

    ok, message = manager.run_frontend_build(str(tmp_path))

    assert ok is False
    assert "npm" in message


def test_frontend_build_propagates_nonzero_exit(monkeypatch, tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(manager, "_resolve_npm_executable", lambda: "npm")
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *args, **kwargs: _FakeProcess(["failed\n"], 7))

    ok, message = manager.run_frontend_build(str(tmp_path))

    assert ok is False
    assert "exit code 7" in message


def test_frontend_node26_build_uses_explicit_runtime_and_dist_dir(monkeypatch, tmp_path: Path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return _FakeProcess([], 0)

    monkeypatch.setattr(manager.subprocess, "Popen", fake_popen)

    ok, message = manager.run_frontend_build(
        str(tmp_path),
        npm_executable="C:/ai/runtimes/node-v26/npm.cmd",
        environment={"NEXT_DIST_DIR": ".next-node26", "PATH": "C:/ai/runtimes/node-v26"},
        build_label="Frontend Node 26",
    )

    assert ok is True
    assert "Node 26" in message
    assert captured["command"] == ["C:/ai/runtimes/node-v26/npm.cmd", "run", "build"]
    environment = captured["environment"]
    assert isinstance(environment, dict)
    assert environment["NEXT_DIST_DIR"] == ".next-node26"


def test_default_frontend_service_uses_resolved_node(monkeypatch, tmp_path: Path):
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    monkeypatch.setattr(manager, "_resolve_node_executable", lambda: "C:/Program Files/nodejs/node.exe")

    frontend = next(service for service in manager.build_default_services(str(tmp_path)) if service.key == "frontend")

    assert frontend.command[0] == "C:/Program Files/nodejs/node.exe"


def test_default_node26_frontend_is_isolated(monkeypatch, tmp_path: Path):
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_bytes(b"")
    node26 = "C:/ai/runtimes/node-v26.7.0-win-x64/node.exe"
    monkeypatch.setattr(manager, "_resolve_node26_executable", lambda: node26)

    frontend = next(
        service for service in manager.build_default_services(str(tmp_path)) if service.key == "frontend_node26"
    )

    assert frontend.command[0] == node26
    assert frontend.command[-1] == "3002"
    assert frontend.health_url == "http://127.0.0.1:3002/architecture"
    assert frontend.environment is not None
    assert frontend.environment["NEXT_DIST_DIR"] == ".next-node26"
    assert frontend.environment["PATH"].split(manager.os.pathsep)[0] == "C:/ai/runtimes/node-v26.7.0-win-x64"


def test_default_openvino_service_is_guard_managed(monkeypatch, tmp_path: Path):
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").write_bytes(b"")

    service = next(
        item for item in manager.build_default_services(str(tmp_path)) if item.key == "openvino_npu"
    )

    assert service.name == "OpenVINO Inference + TTS"
    assert service.guard_service_name == "openvino_npu"
    assert service.health_url == "http://127.0.0.1:8040/health"
    assert "openvino_npu" in service.command
    assert manager.START_ORDER.index("openvino_npu") < manager.START_ORDER.index("api")


def test_non_guarded_start_reports_missing_executable_without_raising(monkeypatch, tmp_path: Path):
    config = manager.ServiceConfig(
        key="frontend",
        name="Frontend Web UI",
        category="Frontend",
        command=["missing-node.exe"],
        cwd=str(tmp_path),
        health_url="http://127.0.0.1:65530/health",
    )
    runtime = manager.ServiceRuntime(config)
    monkeypatch.setattr(runtime, "is_process_running", lambda: False)
    monkeypatch.setattr(manager.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")))

    ok, message = runtime.start()

    assert ok is False
    assert "failed to start" in message


def test_non_guarded_restart_reads_current_env_file(monkeypatch, tmp_path: Path):
    (tmp_path / ".env").write_text("DYNAMIC_VALUE=fresh\n", encoding="utf-8")
    captured: dict[str, object] = {}
    config = manager.ServiceConfig(
        key="observer",
        name="Observer",
        category="Observability",
        command=["python", "-m", "service"],
        cwd=str(tmp_path),
        health_url="http://127.0.0.1:65530/health",
    )
    runtime = manager.ServiceRuntime(config)
    monkeypatch.setattr(runtime, "is_process_running", lambda: False)
    monkeypatch.setattr(manager.time, "sleep", lambda _: None)

    def fake_popen(*args, **kwargs):
        captured["environment"] = kwargs["env"]
        return _RunningProcess()

    monkeypatch.setattr(manager.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("DYNAMIC_VALUE", "stale")

    ok, _ = runtime.start()

    assert ok is True
    assert captured["environment"]["DYNAMIC_VALUE"] == "fresh"
