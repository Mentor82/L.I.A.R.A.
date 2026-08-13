#!/usr/bin/env python3
"""LIARA Server Management GUI.

Features:
- Start/Stop/Restart for core LIARA services
- Health polling with status badges
- Unified live log view
- Works with local Python environment (uses current interpreter)
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import ctypes
from dataclasses import dataclass
from tkinter import ttk
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen


POLL_INTERVAL_MS = 3000
HEALTH_TIMEOUT_SECONDS = 2.5
NODE26_PORTABLE_VERSION = "v26.7.0"
NODE26_PORTABLE_HOME = os.path.join(r"C:\ai\runtimes", f"node-{NODE26_PORTABLE_VERSION}-win-x64")


def _resolve_windows_program(executable: str, override_env: str) -> str | None:
    configured = os.getenv(override_env, "").strip()
    discovered = shutil.which(executable)
    candidates = [configured, discovered]
    if os.name == "nt":
        program_files = os.getenv("ProgramFiles", r"C:\Program Files")
        candidates.append(os.path.join(program_files, "nodejs", executable))
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def _resolve_node_executable() -> str | None:
    return _resolve_windows_program("node.exe" if os.name == "nt" else "node", "LIARA_NODE_EXE")


def _resolve_npm_executable() -> str | None:
    return _resolve_windows_program("npm.cmd" if os.name == "nt" else "npm", "LIARA_NPM_EXE")


def _resolve_node26_program(executable: str, override_env: str) -> str | None:
    configured = os.getenv(override_env, "").strip()
    configured_home = os.getenv("LIARA_NODE26_HOME", "").strip()
    candidates = [
        configured,
        os.path.join(configured_home, executable) if configured_home else "",
        os.path.join(NODE26_PORTABLE_HOME, executable),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def _resolve_node26_executable() -> str | None:
    return _resolve_node26_program("node.exe" if os.name == "nt" else "node", "LIARA_NODE26_EXE")


def _resolve_npm26_executable() -> str | None:
    return _resolve_node26_program("npm.cmd" if os.name == "nt" else "npm", "LIARA_NPM26_EXE")


def _node_runtime_environment(node_executable: str, **values: str) -> dict[str, str]:
    node_home = os.path.dirname(node_executable)
    current_path = os.environ.get("PATH", "")
    return {"PATH": os.pathsep.join(part for part in (node_home, current_path) if part), **values}


def run_frontend_build(
    frontend_root: str,
    emit_line: Callable[[str], None] | None = None,
    *,
    npm_executable: str | None = None,
    environment: dict[str, str] | None = None,
    build_label: str = "Frontend",
) -> tuple[bool, str]:
    """Build the Next.js production bundle and stream output to the caller."""
    package_json = os.path.join(frontend_root, "package.json")
    if not os.path.isfile(package_json):
        return False, f"Frontend build: package.json missing in {frontend_root}"

    npm_executable = npm_executable or _resolve_npm_executable()
    if npm_executable is None:
        return False, f"{build_label} build: npm was not found"

    process_environment = os.environ.copy()
    if environment:
        process_environment.update(environment)

    try:
        process = subprocess.Popen(
            [npm_executable, "run", "build"],
            cwd=frontend_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=process_environment,
        )
    except Exception as exc:
        return False, f"{build_label} build could not start: {exc}"

    if process.stdout is not None:
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            if line and emit_line is not None:
                emit_line(line)

    exit_code = process.wait()
    if exit_code == 0:
        return True, f"{build_label} build completed; restart its service to activate it"
    return False, f"{build_label} build failed with exit code {exit_code}"


def _read_env_file(project_root: str) -> dict[str, str]:
    """Read the current project environment without mutating this process."""
    env_path = os.path.join(project_root, ".env")
    if not os.path.isfile(env_path):
        return {}

    values: dict[str, str] = {}
    try:
        with open(env_path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    values[key] = value
    except Exception:
        # Keep GUI startup resilient even with malformed .env entries.
        return {}
    return values


def _load_env_file(project_root: str) -> None:
    """Load initial .env defaults without replacing the GUI's parent environment."""
    for key, value in _read_env_file(project_root).items():
        os.environ.setdefault(key, value)


@dataclass
class ServiceConfig:
    key: str
    name: str
    category: str
    command: list[str]
    cwd: str
    health_url: str
    guard_service_name: str | None = None
    environment: dict[str, str] | None = None


class ServiceRuntime:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._last_pid: int | None = None
        self._owned_process: subprocess.Popen[str] | None = None

    def _is_guard_managed(self) -> bool:
        return bool(self.config.guard_service_name)

    def _guard_service_name(self) -> str:
        return str(self.config.guard_service_name or self.config.key)

    @staticmethod
    def _pid_running(pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                int(pid),
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
            return True
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _port_connectable(self) -> bool:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(self.config.health_url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 80
        except Exception:
            return False

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.7)
        try:
            sock.connect((host, int(port)))
            return True
        except OSError:
            return False
        finally:
            sock.close()

    def _health_port(self) -> int:
        from urllib.parse import urlparse

        parsed = urlparse(self.config.health_url)
        return int(parsed.port or 0)

    def _port_listen_pid(self, port: int) -> int:
        if os.name != "nt" or port <= 0:
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
            if not line or needle not in line:
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            local = parts[1]
            remote = parts[2]
            if not local.endswith(needle):
                continue
            if remote not in ("0.0.0.0:0", "[::]:0", "*:*"):
                continue
            try:
                return int(parts[-1])
            except ValueError:
                continue
        return 0

    def _guard_command(self, command: str) -> list[str]:
        return [
            _preferred_python_executable(self.config.cwd),
            "scripts/service_guard.py",
            command,
            "--service",
            self._guard_service_name(),
            "--repo-root",
            self.config.cwd,
        ]

    def _run_guard(self, command: str) -> tuple[bool, str, dict | None]:
        if not self._is_guard_managed():
            return False, f"{self.config.name}: not guard-managed", None
        try:
            result = subprocess.run(
                self._guard_command(command),
                cwd=self.config.cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except Exception as exc:  # pragma: no cover - runtime safety
            return False, f"{self.config.name}: guard call failed: {exc}", None

        payload: object
        try:
            payload = json.loads((result.stdout or "").strip() or "[]")
        except Exception:
            payload = []

        if isinstance(payload, list):
            entry = next(
                (
                    item
                    for item in payload
                    if isinstance(item, dict) and item.get("service") == self._guard_service_name()
                ),
                None,
            )
        elif isinstance(payload, dict):
            entry = payload
        else:
            entry = None

        if entry is None:
            stderr_tail = (result.stderr or "").strip()
            return False, f"{self.config.name}: guard returned no entry ({stderr_tail})", None

        outcome = str(entry.get("result") or "").lower()
        if command == "status":
            ok = bool(entry.get("pid_running")) and bool(entry.get("connect_ok"))
        elif command == "start":
            ok = outcome in {"started", "already-running"}
        elif command == "stop":
            ok = outcome in {"stopped", "lock-removed"}
        else:
            ok = outcome not in {"error", ""}

        return ok, outcome or "unknown", entry

    def is_process_running(self) -> bool:
        if not self._is_guard_managed():
            port_pid = self._port_listen_pid(self._health_port())
            if port_pid > 0 and self._pid_running(port_pid):
                self._last_pid = port_pid
                return True
            self._last_pid = None
            return False

        ok, _, entry = self._run_guard("status")
        if isinstance(entry, dict):
            pid_value = entry.get("pid")
            if isinstance(pid_value, int):
                self._last_pid = pid_value if pid_value > 0 else None
        return ok

    def start(self) -> tuple[bool, str]:
        if not self._is_guard_managed():
            if self.is_process_running():
                return True, f"{self.config.name}: already-running"

            logs_dir = os.path.join(self.config.cwd, "logs", "services")
            os.makedirs(logs_dir, exist_ok=True)
            log_path = os.path.join(logs_dir, f"{self.config.key}.log")
            log_handle = open(log_path, "a", encoding="utf-8")

            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS

            try:
                process_environment = os.environ.copy()
                # A service restart must see current project configuration even
                # when the long-running manager predates an .env edit.
                process_environment.update(_read_env_file(self.config.cwd))
                if self.config.environment:
                    process_environment.update(self.config.environment)
                process = subprocess.Popen(
                    self.config.command,
                    cwd=self.config.cwd,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    close_fds=True,
                    creationflags=creationflags,
                    env=process_environment,
                )
            except Exception as exc:
                return False, f"{self.config.name}: failed to start: {exc}; see {log_path}"
            finally:
                log_handle.close()

            self._owned_process = process
            self._last_pid = process.pid
            time.sleep(0.6)
            if process.poll() is not None:
                return False, f"{self.config.name}: failed to start; see {log_path}"
            return True, f"{self.config.name}: started (pid {process.pid})"

        ok, _, entry = self._run_guard("start")
        if isinstance(entry, dict):
            pid_value = entry.get("pid")
            if isinstance(pid_value, int) and pid_value > 0:
                self._last_pid = pid_value
            result = str(entry.get("result") or "")
            if ok:
                return True, f"{self.config.name}: {result}"
            error = str(entry.get("error") or result or "start failed")
            return False, f"{self.config.name}: {error}"
        return False, f"{self.config.name}: start failed"

    def stop(self) -> tuple[bool, str]:
        if not self._is_guard_managed():
            pid = self._last_pid or self._port_listen_pid(self._health_port())
            if not pid or not self._pid_running(pid):
                self._last_pid = None
                return True, f"{self.config.name}: already-stopped"

            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                try:
                    os.kill(pid, 15)
                except OSError:
                    pass

            self._last_pid = None
            self._owned_process = None
            return True, f"{self.config.name}: stopped"

        ok, _, entry = self._run_guard("stop")
        self._last_pid = None
        if isinstance(entry, dict):
            result = str(entry.get("result") or "")
            if ok:
                return True, f"{self.config.name}: {result}"
            error = str(entry.get("error") or result or "stop failed")
            return False, f"{self.config.name}: {error}"
        return False, f"{self.config.name}: stop failed"

    def restart(self) -> tuple[bool, str]:
        stop_ok, stop_msg = self.stop()
        if not stop_ok:
            return False, stop_msg
        start_ok, start_msg = self.start()
        return start_ok, start_msg

    def _stream_logs(self) -> None:
        # Guard-managed services are detached; GUI does not own stdout pipes.
        return


class ServerManagerApp:
    def __init__(self, root: tk.Tk, services: list[ServiceConfig]) -> None:
        self.root = root
        self.root.title("LIARA Server Management")
        self.root.geometry("1150x740")
        self.root.minsize(980, 640)

        self.services = {svc.key: ServiceRuntime(svc) for svc in services}
        self.status_labels: dict[str, ttk.Label] = {}
        self.pid_labels: dict[str, ttk.Label] = {}
        self.health_labels: dict[str, ttk.Label] = {}
        self._health_inflight: set[str] = set()
        self.frontend_build_buttons: dict[str, ttk.Button] = {}

        self._build_ui()
        self._schedule_polling()

    def _build_ui(self) -> None:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("Title.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Subtle.TLabel", foreground="#5A6470")
        style.configure("OK.TLabel", foreground="#1D7A46")
        style.configure("Warn.TLabel", foreground="#B36B00")
        style.configure("Err.TLabel", foreground="#C0392B")

        container = ttk.Frame(self.root, padding=14)
        container.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(container)
        header.pack(fill=tk.X)

        ttk.Label(header, text="LIARA Server Management", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text="Start, monitor, and control local services", style="Subtle.TLabel").pack(
            side=tk.LEFT, padx=(14, 0), pady=(6, 0)
        )

        actions = ttk.Frame(header)
        actions.pack(side=tk.RIGHT)

        ttk.Button(actions, text="Start All", command=self._start_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Stop All", command=self._stop_all).pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Restart All", command=self._restart_all).pack(side=tk.LEFT, padx=4)

        services_container = ttk.Frame(container)
        services_container.pack(fill=tk.X, pady=(10, 10))

        grouped: dict[str, list[ServiceRuntime]] = {}
        for runtime in self.services.values():
            grouped.setdefault(runtime.config.category, []).append(runtime)

        category_order = [
            "Core Services",
            "Observability",
            "Frontend",
        ]
        for category in category_order:
            runtimes = grouped.get(category)
            if not runtimes:
                continue
            card = ttk.LabelFrame(services_container, text=category, padding=12)
            card.pack(fill=tk.X, pady=(0, 8))

            for idx, runtime in enumerate(runtimes):
                row = ttk.Frame(card)
                row.grid(row=idx, column=0, sticky="ew", pady=4)
                card.columnconfigure(0, weight=1)

                ttk.Label(row, text=runtime.config.name, width=28).pack(side=tk.LEFT)

                status_label = ttk.Label(row, text="PROCESS: down", style="Err.TLabel", width=14)
                status_label.pack(side=tk.LEFT, padx=(4, 8))
                self.status_labels[runtime.config.key] = status_label

                pid_label = ttk.Label(row, text="pid: -", width=12, style="Subtle.TLabel")
                pid_label.pack(side=tk.LEFT, padx=(0, 8))
                self.pid_labels[runtime.config.key] = pid_label

                health_label = ttk.Label(row, text="HEALTH: unknown", style="Warn.TLabel", width=18)
                health_label.pack(side=tk.LEFT, padx=(0, 12))
                self.health_labels[runtime.config.key] = health_label

                ttk.Button(row, text="Start", command=lambda k=runtime.config.key: self._start_service(k)).pack(
                    side=tk.LEFT, padx=2
                )
                ttk.Button(row, text="Stop", command=lambda k=runtime.config.key: self._stop_service(k)).pack(
                    side=tk.LEFT, padx=2
                )
                ttk.Button(
                    row, text="Restart", command=lambda k=runtime.config.key: self._restart_service(k)
                ).pack(side=tk.LEFT, padx=2)
                if runtime.config.key in {"frontend", "frontend_node26"}:
                    button_text = "Build Test" if runtime.config.key == "frontend_node26" else "Build"
                    build_button = ttk.Button(
                        row,
                        text=button_text,
                        command=lambda k=runtime.config.key: self._build_frontend(k),
                    )
                    build_button.pack(side=tk.LEFT, padx=2)
                    self.frontend_build_buttons[runtime.config.key] = build_button

        log_card = ttk.LabelFrame(container, text="Live Log", padding=8)
        log_card.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_card, wrap=tk.NONE, bg="#101318", fg="#D7DFEA", insertbackground="#D7DFEA")
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(font=("Consolas", 10))

        self._append_log("[system] GUI ready")

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"{timestamp} {message}\n")
        self.log_text.see(tk.END)

    def _run_with_callback(
        self,
        worker: Callable[[], tuple[bool, str]],
        on_done: Callable[[tuple[bool, str]], None],
    ) -> None:
        """Execute potentially blocking operations off the UI thread.

        Tk updates must happen on main thread; this helper marshals completion
        back via root.after callback.
        """

        def _target() -> None:
            try:
                result = worker()
            except Exception as exc:  # pragma: no cover - runtime safety
                result = (False, f"operation failed: {exc}")
            self.root.after(0, lambda: on_done(result))

        threading.Thread(target=_target, daemon=True).start()

    def _request_health(self, url: str) -> tuple[bool, str]:
        req = Request(url, method="GET")
        try:
            with urlopen(req, timeout=HEALTH_TIMEOUT_SECONDS) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except URLError:
            return False, "down"
        except Exception:
            return False, "down"

        try:
            payload = json.loads(raw)
            status = payload.get("status")
            if isinstance(status, str) and status in {"ok", "success", "partial"}:
                return True, status
        except Exception:
            pass

        return True, "up"

    def _start_service(self, key: str) -> None:
        runtime = self.services[key]
        self._append_log(f"[system] [{runtime.config.key}] start requested")

        def _done(result: tuple[bool, str]) -> None:
            ok, message = result
            self._append_log(f"[system] {message}")
            if ok:
                self._update_process_status()

        self._run_with_callback(runtime.start, _done)

    def _stop_service(self, key: str) -> None:
        runtime = self.services[key]
        self._append_log(f"[system] [{runtime.config.key}] stop requested")

        def _done(result: tuple[bool, str]) -> None:
            ok, message = result
            self._append_log(f"[system] {message}")
            if ok:
                self._update_process_status()

        self._run_with_callback(runtime.stop, _done)

    def _restart_service(self, key: str) -> None:
        runtime = self.services[key]
        self._append_log(f"[system] [{runtime.config.key}] restart requested")

        def _done(result: tuple[bool, str]) -> None:
            ok, message = result
            self._append_log(f"[system] {message}")
            if ok:
                self._update_process_status()

        self._run_with_callback(runtime.restart, _done)

    def _build_frontend(self, service_key: str = "frontend") -> None:
        runtime = self.services.get(service_key)
        if runtime is None:
            self._append_log(f"[system] [{service_key}] build unavailable: service not configured")
            return

        is_node26 = service_key == "frontend_node26"
        idle_text = "Build Test" if is_node26 else "Build"
        build_label = "Frontend Node 26" if is_node26 else "Frontend"
        npm_executable = _resolve_npm26_executable() if is_node26 else _resolve_npm_executable()
        if is_node26 and npm_executable is None:
            self._append_log("[system] [frontend_node26] build unavailable: portable npm was not found")
            return
        button = self.frontend_build_buttons.get(service_key)
        if button is not None:
            button.configure(state=tk.DISABLED, text="Building...")
        self._append_log(f"[system] [{service_key}] production build requested")

        def _emit(line: str) -> None:
            self.root.after(0, lambda value=line: self._append_log(f"[{service_key}:build] {value}"))

        def _worker() -> tuple[bool, str]:
            return run_frontend_build(
                runtime.config.cwd,
                _emit,
                npm_executable=npm_executable,
                environment=runtime.config.environment,
                build_label=build_label,
            )

        def _done(result: tuple[bool, str]) -> None:
            ok, message = result
            self._append_log(f"[system] {message}")
            if button is not None:
                button.configure(state=tk.NORMAL, text=idle_text)
            if ok and runtime.is_process_running():
                self._append_log(f"[system] [{service_key}] build is ready; use Restart to load it")

        self._run_with_callback(_worker, _done)

    def _wait_for_healthy(self, key: str) -> bool:
        """Block (in a background thread) until the service returns HTTP 200 or timeout."""
        runtime = self.services[key]
        url = runtime.config.health_url
        deadline = time.monotonic() + HEALTH_WAIT_TIMEOUT_SECONDS
        attempt = 0
        while time.monotonic() < deadline:
            ok, detail = self._request_health(url)
            attempt += 1
            if ok:
                self.root.after(
                    0,
                    lambda k=key, d=detail, a=attempt: self._append_log(
                        f"[system] [{k}] healthy ({d}) after {a} poll(s)"
                    ),
                )
                return True
            time.sleep(HEALTH_POLL_INTERVAL_SECONDS)
        self.root.after(
            0,
            lambda k=key: self._append_log(
                f"[system] [{k}] health timeout after {HEALTH_WAIT_TIMEOUT_SECONDS}s — proceeding anyway"
            ),
        )
        return False

    def _start_all(self) -> None:
        """Start services sequentially in defined start order, waiting for health before next."""
        self._append_log("[system] Starting all services in order: " + " → ".join(START_ORDER))
        ordered_keys = [k for k in START_ORDER if k in self.services]

        def _sequential_start() -> None:
            for key in ordered_keys:
                runtime = self.services[key]
                self.root.after(0, lambda k=key: self._append_log(f"[system] [{k}] start requested"))
                ok, message = runtime.start()
                self.root.after(0, lambda m=message: self._append_log(f"[system] {m}"))
                self.root.after(0, self._update_process_status)
                if ok:
                    self._wait_for_healthy(key)

        threading.Thread(target=_sequential_start, daemon=True).start()

    def _stop_all(self) -> None:
        """Stop all services (parallel, reverse start order)."""
        for key in reversed(START_ORDER):
            if key in self.services:
                self._stop_service(key)

    def _restart_all(self) -> None:
        """Stop all services, then start them sequentially in start order."""
        self._append_log("[system] Restarting all services: stopping first ...")
        stop_order = [k for k in reversed(START_ORDER) if k in self.services]
        start_order = [k for k in START_ORDER if k in self.services]

        def _sequential_restart() -> None:
            # Phase 1: stop all in reverse order
            for key in stop_order:
                runtime = self.services[key]
                self.root.after(0, lambda k=key: self._append_log(f"[system] [{k}] stop requested"))
                ok, message = runtime.stop()
                self.root.after(0, lambda m=message: self._append_log(f"[system] {m}"))
            self.root.after(0, self._update_process_status)
            time.sleep(1.0)
            # Phase 2: start all in correct order
            self.root.after(0, lambda: self._append_log("[system] Restart: starting services in order ..."))
            for key in start_order:
                runtime = self.services[key]
                self.root.after(0, lambda k=key: self._append_log(f"[system] [{k}] start requested"))
                ok, message = runtime.start()
                self.root.after(0, lambda m=message: self._append_log(f"[system] {m}"))
                self.root.after(0, self._update_process_status)
                if ok:
                    self._wait_for_healthy(key)

        threading.Thread(target=_sequential_restart, daemon=True).start()

    def _drain_logs(self) -> None:
        for runtime in self.services.values():
            while True:
                try:
                    line = runtime.log_queue.get_nowait()
                except queue.Empty:
                    break
                self._append_log(line)

    def _update_process_status(self) -> None:
        for key, runtime in self.services.items():
            running = runtime.is_process_running()
            if running:
                self.status_labels[key].configure(text="PROCESS: up", style="OK.TLabel")
                pid_label = runtime._last_pid if runtime._last_pid is not None else "?"
                self.pid_labels[key].configure(text=f"pid: {pid_label}")
            else:
                self.status_labels[key].configure(text="PROCESS: down", style="Err.TLabel")
                self.pid_labels[key].configure(text="pid: -")

    def _update_health_status(self) -> None:
        for key, runtime in self.services.items():
            if key in self._health_inflight:
                continue

            self._health_inflight.add(key)

            def _worker(url: str = runtime.config.health_url) -> tuple[bool, str]:
                return self._request_health(url)

            def _done(result: tuple[bool, str], service_key: str = key) -> None:
                self._health_inflight.discard(service_key)
                ok, detail = result
                label = self.health_labels[service_key]
                if ok:
                    label.configure(text=f"HEALTH: {detail}", style="OK.TLabel")
                else:
                    label.configure(text="HEALTH: down", style="Err.TLabel")

            self._run_with_callback(_worker, _done)

    def _schedule_polling(self) -> None:
        self._drain_logs()
        self._update_process_status()
        self._update_health_status()
        self.root.after(POLL_INTERVAL_MS, self._schedule_polling)


# Start order: core foundation first, then observers, frontend last.
START_ORDER = [
    "memory",
    "embedding",
    "openvino_npu",
    "api",
    "bridge",
    "heartbeat",
    "self_observer",
    "frontend",
    "frontend_node26",
]
HEALTH_WAIT_TIMEOUT_SECONDS = 120  # max wait per service before proceeding
HEALTH_POLL_INTERVAL_SECONDS = 1.5


def build_default_services(project_root: str) -> list[ServiceConfig]:
    py = _preferred_python_executable(project_root)
    embedding_exe = os.path.join(project_root, "workers", "embedding", "exec", "bin", "LiaraEmbeddingService.exe")
    embedding_cfg = os.path.join(project_root, "workers", "embedding", "exec", "conf", "embedding_config.toml")
    frontend_root = os.path.join(project_root, "frontend", "web-ui")
    node_executable = _resolve_node_executable() or "node"
    node26_executable = _resolve_node26_executable() or os.path.join(NODE26_PORTABLE_HOME, "node.exe")
    node26_environment = _node_runtime_environment(node26_executable, NEXT_DIST_DIR=".next-node26")

    return [
        ServiceConfig(
            key="memory",
            name="LIARA Memory",
            category="Core Services",
            command=[
                py,
                "scripts/service_guard.py",
                "start",
                "--service",
                "memory",
                "--repo-root",
                project_root,
            ],
            cwd=project_root,
            health_url="http://127.0.0.1:8020/health",
            guard_service_name="memory",
        ),
        ServiceConfig(
            key="embedding",
            name="LIARA Embedding",
            command=[
                embedding_exe,
                f"--config={embedding_cfg}",
            ],
            category="Core Services",
            cwd=project_root,
            health_url="http://127.0.0.1:8030/health",
            guard_service_name="embedding",
        ),
        ServiceConfig(
            key="openvino_npu",
            name="OpenVINO Inference + TTS",
            category="Core Services",
            command=[
                py,
                "scripts/service_guard.py",
                "start",
                "--service",
                "openvino_npu",
                "--repo-root",
                project_root,
            ],
            cwd=project_root,
            health_url="http://127.0.0.1:8040/health",
            guard_service_name="openvino_npu",
        ),
        ServiceConfig(
            key="api",
            name="LIARA API",
            category="Core Services",
            command=[
                py,
                "scripts/service_guard.py",
                "start",
                "--service",
                "api",
                "--repo-root",
                project_root,
            ],
            cwd=project_root,
            health_url="http://127.0.0.1:8010/health",
            guard_service_name="api",
        ),
        ServiceConfig(
            key="proxy",
            name="API Proxy Gateway [8080]",
            category="Core Services",
            command=[
                py,
                "scripts/service_guard.py",
                "start",
                "--service",
                "proxy",
                "--repo-root",
                project_root,
            ],
            cwd=project_root,
            health_url="http://127.0.0.1:8080/",
            guard_service_name="proxy",
        ),
        ServiceConfig(
            key="bridge",
            name="OpenAI Bridge",
            category="Core Services",
            command=[
                py,
                "scripts/service_guard.py",
                "start",
                "--service",
                "bridge",
                "--repo-root",
                project_root,
            ],
            cwd=project_root,
            health_url="http://127.0.0.1:8011/health",
            guard_service_name="bridge",
        ),
        ServiceConfig(
            key="heartbeat",
            name="Resource Heartbeat",
            category="Observability",
            command=[
                py,
                "-m",
                "uvicorn",
                "services.heartbeat.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8050",
                "--log-level",
                "warning",
            ],
            cwd=project_root,
            health_url="http://127.0.0.1:8050/health",
        ),
        ServiceConfig(
            key="self_observer",
            name="Self Observer",
            category="Observability",
            command=[
                py,
                "-m",
                "uvicorn",
                "services.self_observer.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                "8060",
            ],
            cwd=project_root,
            health_url="http://127.0.0.1:8060/health",
        ),
        ServiceConfig(
            key="frontend",
            name="Frontend Web UI",
            category="Frontend",
            command=[
                node_executable,
                "node_modules/next/dist/bin/next",
                "start",
                "-p",
                "3001",
            ],
            cwd=frontend_root,
            health_url="http://127.0.0.1:3001/architecture",
        ),
        ServiceConfig(
            key="frontend_node26",
            name="Frontend Web UI [Node 26]",
            category="Frontend",
            command=[
                node26_executable,
                "node_modules/next/dist/bin/next",
                "start",
                "-p",
                "3002",
            ],
            cwd=frontend_root,
            health_url="http://127.0.0.1:3002/architecture",
            environment=node26_environment,
        ),
    ]


def _preferred_python_executable(project_root: str) -> str:
    """Resolve best Python interpreter, honoring optional env overrides first."""
    configured = os.getenv("LIARA_PYTHON_EXE", "").strip()
    candidates = [
        configured,
        os.path.join(project_root, ".venv", "Scripts", "python.exe"),
        os.path.join(os.path.dirname(project_root), ".venv", "Scripts", "python.exe"),
        sys.executable,
    ]

    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.isfile(candidate):
            return candidate

    return sys.executable


def main() -> int:
    project_root = os.path.dirname(os.path.abspath(__file__))
    _load_env_file(project_root)
    root = tk.Tk()
    app = ServerManagerApp(root, build_default_services(project_root))

    def _on_close() -> None:
        app._stop_all()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
