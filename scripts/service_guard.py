"""
Windows-friendly service guard for LIARA core uvicorn services.

Goals:
- prevent duplicate starts via lock files + mutex
- provide deterministic start/stop/status/recover commands
- centralize service lifecycle so tasks/scripts use one control plane
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Optional


@dataclass(frozen=True)
class ServiceDef:
    name: str
    module: str
    port: int
    host: str = "127.0.0.1"
    launcher: str | None = None


SERVICE_ORDER = ["memory", "embedding", "openvino_npu", "api", "proxy", "bridge"]
SERVICES: Dict[str, ServiceDef] = {
    "api": ServiceDef(name="api", module="services.api.app:app", port=8010),
    "proxy": ServiceDef(name="proxy", module="scripts.proxy_8080_to_8010:app", port=8080, host="0.0.0.0"),
    "bridge": ServiceDef(name="bridge", module="scripts.continue_openai_bridge:app", port=8011),
    "memory": ServiceDef(name="memory", module="services.memory.app:app", port=8020),
    "embedding": ServiceDef(name="embedding", module="native:LiaraEmbeddingService", port=8030),
    "openvino_npu": ServiceDef(
        name="openvino_npu",
        module="services.inference.openvino_npu_app:app",
        port=8040,
        launcher="scripts/start_openvino_npu_instance.ps1",
    ),
}

STALE_HEARTBEAT_SECONDS = 120


class GuardError(RuntimeError):
    pass


class NamedMutex:
    """Best-effort global mutex on Windows to serialize start/stop actions."""

    def __init__(self, name: str):
        self.name = name
        self._handle = None

    def __enter__(self):
        if os.name != "nt":
            return self
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        handle = kernel32.CreateMutexW(None, False, f"Global\\{self.name}")
        if not handle:
            raise GuardError(f"Failed to create mutex {self.name}")
        wait = kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 10000)
        # WAIT_OBJECT_0 = 0, WAIT_ABANDONED = 0x80
        if wait not in (0, 0x80):
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            raise GuardError(f"Failed waiting for mutex {self.name}; code={wait}")
        self._handle = handle
        return self

    def __exit__(self, exc_type, exc, tb):
        if os.name != "nt" or not self._handle:
            return
        kernel32 = ctypes.windll.kernel32
        kernel32.ReleaseMutex(ctypes.c_void_p(self._handle))
        kernel32.CloseHandle(ctypes.c_void_p(self._handle))
        self._handle = None


class ServiceGuard:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.logs_dir = repo_root / "logs" / "services"
        self.lock_dir = repo_root / "logs" / "service_locks"
        self.crash_dir = repo_root / "logs" / "crash_reports"
        self.guard_audit_path = self.logs_dir / "service_guard.jsonl"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.crash_dir.mkdir(parents=True, exist_ok=True)

    def append_audit_event(self, *, command: str, service: str, result: dict) -> None:
        """Append one structured JSONL event for guard actions (best-effort)."""
        outcome = result.get("result")
        if outcome is None and command == "status":
            outcome = "healthy" if bool(result.get("connect_ok")) else "not-connectable"
        event = {
            "timestamp": self._now_iso(),
            "source": "service_guard",
            "command": command,
            "service": service,
            "result": str(outcome or "unknown"),
            "payload": result,
        }
        try:
            with open(self.guard_audit_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=True) + "\n")
        except Exception:
            # Never fail service control operations just because audit logging failed.
            return

    def lock_path(self, service: ServiceDef) -> Path:
        return self.lock_dir / f"{service.name}.lock.json"

    def _python_executable(self) -> str:
        preferred = self.repo_root / ".venv" / "Scripts" / "python.exe"
        if preferred.exists():
            return str(preferred)
        return sys.executable

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _load_lock(self, service: ServiceDef) -> Optional[dict]:
        path = self.lock_path(service)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write_lock(self, service: ServiceDef, payload: dict) -> None:
        self.lock_path(service).write_text(
            json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8"
        )

    def _write_crash_marker(self, service: ServiceDef, payload: dict) -> Path:
        ts = self._now_iso().replace(":", "").replace(".", "_")
        path = self.crash_dir / f"{service.name}-{ts}.json"
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        return path

    def _parse_iso_ts(self, raw: object) -> Optional[datetime]:
        if not isinstance(raw, str) or not raw.strip():
            return None
        normalized = raw.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    def _is_heartbeat_stale(self, lock: dict) -> bool:
        heartbeat = self._parse_iso_ts(lock.get("heartbeat_ts"))
        if heartbeat is None:
            heartbeat = self._parse_iso_ts(lock.get("started_at"))
        if heartbeat is None:
            return True
        age = (datetime.now(timezone.utc) - heartbeat.astimezone(timezone.utc)).total_seconds()
        return age > STALE_HEARTBEAT_SECONDS

    def _remove_lock(self, service: ServiceDef) -> None:
        path = self.lock_path(service)
        if path.exists():
            path.unlink(missing_ok=True)

    def _pid_running(self, pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            # Avoid locale/encoding issues from tasklist parsing.
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                int(pid),
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _port_connectable(self, host: str, port: int) -> bool:
        connect_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.7)
        try:
            sock.connect((connect_host, port))
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def _port_bind_free(self, host: str, port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def _port_listen_pid(self, port: int) -> int:
        if os.name != "nt":
            return 0
        try:
            output = subprocess.check_output(
                ["netstat", "-ano", "-p", "tcp"],
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            return 0
        needle = f":{port}"
        for raw in output.splitlines():
            line = raw.strip()
            if not line:
                continue
            if needle not in line:
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            local = parts[1]
            remote = parts[2]
            if not local.endswith(needle):
                continue
            # netstat output is localized (e.g. ABHOEREN on German Windows),
            # but LISTEN rows consistently use a wildcard remote endpoint.
            remote_is_wildcard = remote in ("0.0.0.0:0", "[::]:0", "*:*")
            if not remote_is_wildcard:
                continue
            try:
                return int(parts[-1])
            except ValueError:
                continue
        return 0

    def _embedding_exec_paths(self) -> tuple[Path, Path, Path]:
        root = self.repo_root / "workers" / "embedding" / "exec"
        return (
            root / "bin" / "LiaraEmbeddingService.exe",
            root / "conf" / "embedding_config.toml",
            root / "lib",
        )

    def _build_command(self, service: ServiceDef, reload_mode: bool) -> list[str]:
        if service.name == "embedding":
            exe_path, config_path, _lib_dir = self._embedding_exec_paths()
            return [str(exe_path), f"--config={config_path}"]
        if service.launcher:
            return [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.repo_root / service.launcher),
            ]

        cmd = [
            self._python_executable(),
            "-m",
            "uvicorn",
            service.module,
            "--host",
            service.host,
            "--port",
            str(service.port),
            "--log-level",
            "info",
        ]
        if reload_mode:
            cmd.append("--reload")
        return cmd

    def _build_start_env(self, service: ServiceDef) -> Optional[dict]:
        if service.name != "embedding":
            return None
        _exe_path, _config_path, lib_dir = self._embedding_exec_paths()
        env = dict(os.environ)
        existing_path = env.get("PATH", "")
        env["PATH"] = f"{lib_dir};{existing_path}" if existing_path else str(lib_dir)
        return env

    def _spawn_exit_monitor(
        self,
        *,
        service: ServiceDef,
        pid: int,
        started_at: str,
        log_path: Path,
    ) -> None:
        if os.name != "nt":
            return
        if not service.module.startswith("native:"):
            return
        cmd = [
            self._python_executable(),
            str(Path(__file__).resolve()),
            "monitor-exit",
            "--service",
            service.name,
            "--repo-root",
            str(self.repo_root),
            "--pid",
            str(pid),
            "--started-at",
            started_at,
            "--log-file",
            str(log_path),
        ]
        try:
            subprocess.Popen(
                cmd,
                cwd=str(self.repo_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                close_fds=True,
            )
        except Exception as exc:
            self.append_audit_event(
                command="monitor-exit",
                service=service.name,
                result={
                    "service": service.name,
                    "action": "monitor-exit",
                    "result": "monitor-start-failed",
                    "pid": pid,
                    "error": str(exc),
                },
            )

    def monitor_native_exit(
        self,
        *,
        service: ServiceDef,
        pid: int,
        started_at: str,
        log_file: str,
    ) -> dict:
        if os.name != "nt":
            return {
                "service": service.name,
                "action": "monitor-exit",
                "result": "unsupported-platform",
                "pid": pid,
            }

        SYNCHRONIZE = 0x00100000
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        INFINITE = 0xFFFFFFFF
        STILL_ACTIVE = 259

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
            False,
            int(pid),
        )
        if not handle:
            result = {
                "service": service.name,
                "action": "monitor-exit",
                "result": "process-not-found",
                "pid": pid,
                "started_at": started_at,
                "ended_at": self._now_iso(),
                "log_file": log_file,
            }
            self.append_audit_event(command="monitor-exit", service=service.name, result=result)
            self._write_crash_marker(service, result)
            return result

        try:
            kernel32.WaitForSingleObject(ctypes.c_void_p(handle), INFINITE)
            exit_code = ctypes.c_ulong(STILL_ACTIVE)
            got_exit_code = bool(kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(exit_code)))
        finally:
            kernel32.CloseHandle(ctypes.c_void_p(handle))

        result = {
            "service": service.name,
            "action": "monitor-exit",
            "result": "process-exited",
            "pid": pid,
            "started_at": started_at,
            "ended_at": self._now_iso(),
            "exit_code": int(exit_code.value) if got_exit_code else None,
            "log_file": log_file,
            "lock_file": str(self.lock_path(service)),
        }
        marker_path = self._write_crash_marker(service, result)
        result["crash_marker"] = str(marker_path)
        self.append_audit_event(command="monitor-exit", service=service.name, result=result)
        return result

    def _wsl_mode_enabled(self) -> bool:
        mode = (os.environ.get("LIARA_SANDBOX_MODE", "") or "").strip().lower()
        if not mode:
            # Keep behavior aligned with runtime default on Windows.
            return False
        return mode == "wsl"

    def _ensure_wsl_distro_ready(self) -> None:
        if os.name != "nt":
            return
        if not self._wsl_mode_enabled():
            return

        distro = (os.environ.get("LIARA_WSL_DISTRO", "Debian") or "Debian").strip() or "Debian"
        def _probe(timeout_seconds: int) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["wsl.exe", "-d", distro, "-e", "sh", "-lc", "echo LIARA_WSL_READY"],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )

        try:
            result = _probe(25)
        except subprocess.TimeoutExpired:
            # Try one self-heal cycle for a hung WSL daemon/distro.
            try:
                subprocess.run(
                    ["wsl.exe", "--shutdown"],
                    cwd=str(self.repo_root),
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise GuardError(
                    f"api preflight: WSL shutdown timed out while recovering distro '{distro}'"
                ) from exc
            try:
                result = _probe(40)
            except Exception as exc:
                raise GuardError(
                    f"api preflight: WSL distro '{distro}' is not reachable after restart ({exc})"
                ) from exc
        except Exception as exc:
            raise GuardError(
                f"api preflight: WSL distro '{distro}' is not reachable ({exc})"
            ) from exc

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if stderr:
                raise GuardError(
                    f"api preflight: WSL distro '{distro}' failed to start/reach ({stderr})"
                )
            raise GuardError(
                f"api preflight: WSL distro '{distro}' failed to start/reach"
            )

    def status_one(self, service: ServiceDef) -> dict:
        lock = self._load_lock(service)
        pid = int(lock.get("pid", 0)) if isinstance(lock, dict) else 0
        pid_running = self._pid_running(pid)
        connect_ok = self._port_connectable(service.host, service.port)
        bind_free = self._port_bind_free(service.host, service.port)

        # Allow status to report real runtime even when lock is missing/stale.
        if (pid <= 0 or not pid_running) and connect_ok:
            inferred_pid = self._port_listen_pid(service.port)
            if inferred_pid > 0 and self._pid_running(inferred_pid):
                pid = inferred_pid
                pid_running = True

        stale = bool(lock) and (not pid_running and not connect_ok)

        # Refresh heartbeat when service is healthy and lock is still owned.
        if isinstance(lock, dict) and lock and pid_running and connect_ok:
            lock["heartbeat_ts"] = self._now_iso()
            lock["last_status_check_ts"] = self._now_iso()
            self._write_lock(service, lock)
        elif isinstance(lock, dict) and lock:
            lock["last_status_check_ts"] = self._now_iso()
            self._write_lock(service, lock)

        heartbeat_stale = bool(lock) and isinstance(lock, dict) and self._is_heartbeat_stale(lock)
        stale = bool(stale or (heartbeat_stale and not connect_ok))
        return {
            "service": service.name,
            "port": service.port,
            "pid": pid,
            "lock_present": bool(lock),
            "pid_running": pid_running,
            "connect_ok": connect_ok,
            "bind_free": bind_free,
            "stale_lock": stale,
            "heartbeat_stale": bool(heartbeat_stale),
            "lock": lock or {},
        }

    def _ensure_not_busy(self, service: ServiceDef) -> None:
        if not self._port_bind_free(service.host, service.port):
            raise GuardError(
                f"{service.name}: port {service.port} is in use; run recover/stop first"
            )

    def _wait_until_connectable(self, service: ServiceDef, timeout_seconds: float = 30.0) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._port_connectable(service.host, service.port):
                return True
            time.sleep(0.25)
        return False

    def _wait_until_stopped(
        self,
        service: ServiceDef,
        pid: int,
        timeout_seconds: float = 10.0,
    ) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if not self._pid_running(pid) and self._port_bind_free(service.host, service.port):
                return True
            time.sleep(0.1)
        return not self._pid_running(pid) and self._port_bind_free(service.host, service.port)

    def start_one(self, service: ServiceDef, reload_mode: bool = False) -> dict:
        if service.name == "api":
            self._ensure_wsl_distro_ready()

        lock = self._load_lock(service)
        if lock:
            pid = int(lock.get("pid", 0))
            if pid and self._pid_running(pid):
                return {
                    "service": service.name,
                    "action": "start",
                    "result": "already-running",
                    "pid": pid,
                }
            # stale lock cleanup before a new start
            self._remove_lock(service)

        if service.name in {"embedding", "openvino_npu"}:
            existing_pid = self._port_listen_pid(service.port)
            if existing_pid > 0 and self._pid_running(existing_pid):
                payload = {
                    "service": service.name,
                    "pid": existing_pid,
                    "port": service.port,
                    "host": service.host,
                    "module": service.module,
                    "command": [],
                    "log_file": str(self.logs_dir / f"{service.name}.log"),
                    "started_at": self._now_iso(),
                    "heartbeat_ts": self._now_iso(),
                    "last_status_check_ts": self._now_iso(),
                    "adopted": True,
                }
                self._write_lock(service, payload)
                return {
                    "service": service.name,
                    "action": "start",
                    "result": "already-running",
                    "pid": existing_pid,
                }

        self._ensure_not_busy(service)

        log_path = self.logs_dir / f"{service.name}.log"
        log_handle = open(log_path, "a", encoding="utf-8")
        cmd = self._build_command(service, reload_mode=reload_mode)

        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            if service.launcher:
                creationflags |= subprocess.CREATE_NO_WINDOW
            else:
                creationflags |= subprocess.DETACHED_PROCESS

        try:
            process = subprocess.Popen(
                cmd,
                cwd=str(self.repo_root),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                env=self._build_start_env(service),
                creationflags=creationflags,
                close_fds=True,
            )
        finally:
            # Child keeps the inherited handle; parent should close its copy.
            log_handle.close()

        # Give uvicorn a moment to bind and fail fast if startup crashes.
        time.sleep(0.7)
        if process.poll() is not None:
            raise GuardError(f"{service.name}: failed to start; see {log_path}")

        if not self._wait_until_connectable(service):
            # Best-effort cleanup if process is alive but not serving.
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    try:
                        os.kill(process.pid, 15)
                    except OSError:
                        pass
            raise GuardError(
                f"{service.name}: startup timeout (port {service.port} not connectable); see {log_path}"
            )

        started_at = self._now_iso()
        payload = {
            "service": service.name,
            "pid": process.pid,
            "port": service.port,
            "host": service.host,
            "module": service.module,
            "command": cmd,
            "log_file": str(log_path),
            "started_at": started_at,
            "heartbeat_ts": self._now_iso(),
            "last_status_check_ts": self._now_iso(),
        }
        self._write_lock(service, payload)
        self._spawn_exit_monitor(
            service=service,
            pid=process.pid,
            started_at=started_at,
            log_path=log_path,
        )
        return {
            "service": service.name,
            "action": "start",
            "result": "started",
            "pid": process.pid,
            "port": service.port,
        }

    def stop_one(self, service: ServiceDef) -> dict:
        lock = self._load_lock(service)
        pid = int(lock.get("pid", 0)) if isinstance(lock, dict) else 0

        killed = False
        if pid > 0 and self._pid_running(pid):
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
                killed = self._wait_until_stopped(service, pid)
            else:
                try:
                    os.kill(pid, 15)
                    killed = self._wait_until_stopped(service, pid)
                except OSError:
                    killed = False

            if not killed:
                raise GuardError(
                    f"{service.name}: stop timeout; pid {pid} or port {service.port} is still active"
                )

        self._remove_lock(service)
        return {
            "service": service.name,
            "action": "stop",
            "result": "stopped" if killed else "lock-removed",
            "pid": pid,
        }

    def recover_one(self, service: ServiceDef) -> dict:
        status = self.status_one(service)
        actions: list[str] = []

        if status["stale_lock"]:
            self._remove_lock(service)
            actions.append("removed-stale-lock")

        # If lock pid exists but is dead, clear lock.
        if status["lock_present"] and not status["pid_running"]:
            self._remove_lock(service)
            actions.append("removed-dead-pid-lock")

        # If port remains occupied and no lock pid is running, report orphan.
        # We do not hard-kill unknown PIDs here.
        still_busy = not self._port_bind_free(service.host, service.port)
        if still_busy and not status["pid_running"]:
            actions.append("orphan-port-detected")

        return {
            "service": service.name,
            "action": "recover",
            "result": "ok",
            "actions": actions,
            "port_busy": still_busy,
        }


def _service_iter(name: str) -> Iterable[ServiceDef]:
    if name == "all":
        for key in SERVICE_ORDER:
            yield SERVICES[key]
        return
    if name not in SERVICES:
        raise GuardError(f"Unknown service: {name}")
    yield SERVICES[name]


def _print_json(obj: object) -> None:
    print(json.dumps(obj, ensure_ascii=True, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LIARA service guard")
    parser.add_argument(
        "command",
        choices=["start", "stop", "status", "recover", "start-all", "stop-all", "monitor-exit"],
    )
    parser.add_argument("--service", choices=[*SERVICES.keys(), "all"], default="all")
    parser.add_argument("--reload", action="store_true", help="Start uvicorn with --reload")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--pid", type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument("--started-at", default="", help=argparse.SUPPRESS)
    parser.add_argument("--log-file", default="", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    guard = ServiceGuard(repo_root=Path(args.repo_root))

    command = args.command
    service_name = args.service
    if command == "monitor-exit":
        if service_name not in SERVICES or args.pid <= 0:
            _print_json({"result": "error", "error": "monitor-exit requires --service and --pid"})
            return 2
        result = guard.monitor_native_exit(
            service=SERVICES[service_name],
            pid=args.pid,
            started_at=args.started_at,
            log_file=args.log_file,
        )
        _print_json(result)
        return 0

    if command == "start-all":
        command = "start"
        service_name = "all"
    elif command == "stop-all":
        command = "stop"
        service_name = "all"

    mutex_name = f"liara-service-guard-{service_name}-{command}"
    results = []
    exit_code = 0

    try:
        with NamedMutex(mutex_name):
            iterable = list(_service_iter(service_name))
            if command == "stop":
                iterable = list(reversed(iterable))

            for service in iterable:
                try:
                    if command == "start":
                        result = guard.start_one(service, reload_mode=args.reload)
                        results.append(result)
                        guard.append_audit_event(command=command, service=service.name, result=result)
                    elif command == "stop":
                        result = guard.stop_one(service)
                        results.append(result)
                        guard.append_audit_event(command=command, service=service.name, result=result)
                    elif command == "status":
                        result = guard.status_one(service)
                        results.append(result)
                        guard.append_audit_event(command=command, service=service.name, result=result)
                    elif command == "recover":
                        result = guard.recover_one(service)
                        results.append(result)
                        guard.append_audit_event(command=command, service=service.name, result=result)
                    else:
                        raise GuardError(f"Unsupported command {command}")
                except GuardError as exc:
                    exit_code = 1
                    error_result = {
                        "service": service.name,
                        "action": command,
                        "result": "error",
                        "error": str(exc),
                    }
                    results.append(error_result)
                    guard.append_audit_event(command=command, service=service.name, result=error_result)
    except GuardError as exc:
        fatal_result = {"service": service_name, "action": command, "result": "error", "error": str(exc)}
        guard.append_audit_event(command=command, service=service_name, result=fatal_result)
        _print_json({"result": "error", "error": str(exc)})
        return 1

    _print_json(results)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
