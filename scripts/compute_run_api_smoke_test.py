"""Smoke test for POST /compute/run using the unified Julia WSL path.

Modes:
- default: call an already running API base URL
- --with-server: start a temporary local uvicorn server for the test
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
PYTHON = REPO / ".venv" / "Scripts" / "python.exe"
DEFAULT_BASE_URL = "http://127.0.0.1:8010"
DEFAULT_PORT = 8032


def _http_json(method: str, url: str, payload: dict | None = None, timeout: float = 20.0) -> tuple[int, dict | str]:
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return int(resp.status), json.loads(raw)
            except json.JSONDecodeError:
                return int(resp.status), raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = raw
        return int(exc.code), body
    except urllib.error.URLError as exc:
        return 0, str(exc.reason)


def _wait_for_health(base_url: str, timeout_s: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status, _body = _http_json("GET", f"{base_url}/health", timeout=3.0)
        if status == 200:
            return True
        time.sleep(0.5)
    return False


def _run_smoke(base_url: str) -> int:
    payload = {
        "model": "turbine_power",
        "inputs": {"shaft_speed_rpm": 1500.0, "torque_nm": 200.0},
    }
    status, body = _http_json("POST", f"{base_url}/compute/run", payload=payload)
    if status != 200 or not isinstance(body, dict):
        print(f"SMOKE_FAIL: HTTP {status} body={body}")
        return 1

    outputs = body.get("outputs") if isinstance(body.get("outputs"), dict) else {}
    power_kw = float(outputs.get("power_kw", -1))
    if abs(power_kw - 31.4159) > 0.02:
        print(f"SMOKE_FAIL: unexpected power_kw={power_kw} body={body}")
        return 1

    print(json.dumps({"status": "ok", "http_status": status, "body": body}, ensure_ascii=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("LIARA_API_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--with-server", action="store_true")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()

    if not args.with_server:
        return _run_smoke(args.base_url)

    base_url = f"http://127.0.0.1:{args.port}"
    server_env = os.environ.copy()
    server_env["LLAMA_CPP_MANAGED_BY_API"] = "false"
    server = subprocess.Popen(
        [
            str(PYTHON),
            "-m",
            "uvicorn",
            "services.api.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
            "--log-level",
            "warning",
        ],
        cwd=str(REPO),
        env=server_env,
    )
    try:
        if not _wait_for_health(base_url):
            print("SMOKE_FAIL: temporary API server did not become healthy in time")
            return 1
        return _run_smoke(base_url)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()


if __name__ == "__main__":
    raise SystemExit(main())
