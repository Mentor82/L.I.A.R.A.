"""Run live stream memory regression check against a temporary local API server.

This script is task-safe for both PowerShell and bash shells because it avoids
fragile inline quoting.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
PREFERRED_API_PORT = 8010  # 8020 is reserved for MEMORY_SERVICE_BASE_URL


def _can_bind_port(port: int) -> bool:
    """Return True when localhost:port is currently bindable."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", int(port)))
            sock.listen(1)
        except OSError:
            return False
    return True


def _is_port_in_use(port: int) -> bool:
    """Return True if localhost:port currently accepts TCP connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


def _pick_api_port(preferred_port: int = PREFERRED_API_PORT) -> int:
    """Pick a free API port, preferring 8010 and falling back locally."""
    if (not _is_port_in_use(preferred_port)) and _can_bind_port(preferred_port):
        return preferred_port

    for candidate in range(preferred_port + 1, preferred_port + 40):
        if (not _is_port_in_use(candidate)) and _can_bind_port(candidate):
            return candidate

    raise RuntimeError("No free local port found for live stream regression API startup.")


def _wait_for_api_ready(api_base: str, *, timeout_seconds: float = 30.0) -> bool:
    deadline = time.time() + timeout_seconds
    health_url = f"{api_base}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2.0) as response:  # nosec B310
                if int(response.status) < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            pass
        time.sleep(0.4)
    return False


def main() -> int:
    api_port = _pick_api_port(PREFERRED_API_PORT)
    api_base = f"http://127.0.0.1:{api_port}"

    if api_port != PREFERRED_API_PORT:
        print(
            (
                "[live_stream_regression_check] preferred port "
                f"{PREFERRED_API_PORT} busy, using fallback port {api_port}"
            ),
            flush=True,
        )

    print(f"[live_stream_regression_check] starting local API on {api_base}", flush=True)
    server = subprocess.Popen(
        [
            str(PYTHON),
            "-m",
            "uvicorn",
            "services.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    env = os.environ.copy()
    env["RUN_LIVE_CHAT_STREAM_MEMORY_TESTS"] = "1"
    env["LIARA_API_BASE_URL"] = api_base

    if not _wait_for_api_ready(api_base, timeout_seconds=30.0):
        server.terminate()
        stdout, stderr = server.communicate(timeout=5)
        print("[live_stream_regression_check] API server did not become ready in time.", file=sys.stderr)
        if stdout:
            print("[uvicorn stdout]", file=sys.stderr)
            print(stdout.strip(), file=sys.stderr)
        if stderr:
            print("[uvicorn stderr]", file=sys.stderr)
            print(stderr.strip(), file=sys.stderr)
        return 2

    try:
        print("[live_stream_regression_check] API ready, running live stream tests", flush=True)
        result = subprocess.run(
            [
                str(PYTHON),
                "-m",
                "pytest",
                "tests/integration/test_chat_stream_memory_effect_live.py",
                "--maxfail=1",
                "-q",
            ],
            cwd=str(REPO),
            env=env,
            timeout=300,
        )
        print(f"[live_stream_regression_check] pytest finished with exit code {result.returncode}", flush=True)
        return int(result.returncode)
    except subprocess.TimeoutExpired:
        print("[live_stream_regression_check] pytest run timed out after 300 seconds.", file=sys.stderr)
        return 124
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
