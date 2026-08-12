"""Live regression check for /tools/{tool}/invoke simulation_mode passthrough.

This script validates that the manual invoke endpoint forwards simulation_mode
into ToolExecutionRequest.

Expected behavior:
- simulation_mode=true returns mock output shape for sys/date time lookup
- simulation_mode=false returns real command output (plain string)
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

import httpx


def _fail(message: str, *, payload: Any | None = None, code: int = 1) -> int:
    print(f"[live_simulation_mode_invoke_check] FAIL: {message}", file=sys.stderr)
    if payload is not None:
        try:
            print(json.dumps(payload, indent=2, ensure_ascii=True), file=sys.stderr)
        except Exception:
            print(str(payload), file=sys.stderr)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description="Live simulation_mode invoke regression check")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010", help="LIARA API base URL")
    parser.add_argument("--timeout", type=float, default=20.0, help="HTTP timeout in seconds")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    timeout = float(args.timeout)

    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        try:
            health = client.get("/health")
        except Exception as exc:  # pragma: no cover - runtime/network dependent
            return _fail(f"health check request failed: {exc}", code=2)

        if health.status_code != 200:
            return _fail("health endpoint is not ready", payload={"status_code": health.status_code, "body": health.text}, code=2)

        request_id_sim = f"live-sim-{uuid.uuid4().hex[:12]}"
        payload_sim = {
            "parameters": {
                "command": "date",
                "context": "agent_time_lookup",
                "request_id": request_id_sim,
                "run_id": request_id_sim,
                "session_id": "live-simulation-mode-invoke-check",
                "source": "script.live_simulation_mode_invoke_check",
            },
            "timeout_seconds": 5,
            "simulation_mode": True,
        }
        response_sim = client.post("/tools/sys/invoke", json=payload_sim)
        if response_sim.status_code != 200:
            return _fail("simulation invoke returned non-200", payload={"status_code": response_sim.status_code, "body": response_sim.text})

        body_sim = response_sim.json()
        sim_output = body_sim.get("output")
        if not isinstance(sim_output, dict):
            return _fail("simulation invoke output must be an object", payload=body_sim)
        if sim_output.get("kind") != "time_lookup" or "utc_iso" not in sim_output:
            return _fail("simulation invoke output shape is unexpected", payload=body_sim)

        request_id_real = f"live-real-{uuid.uuid4().hex[:12]}"
        payload_real = {
            "parameters": {
                "command": "date",
                "context": "agent_time_lookup",
                "request_id": request_id_real,
                "run_id": request_id_real,
                "session_id": "live-simulation-mode-invoke-check",
                "source": "script.live_simulation_mode_invoke_check",
            },
            "timeout_seconds": 5,
            "simulation_mode": False,
        }
        response_real = client.post("/tools/sys/invoke", json=payload_real)
        if response_real.status_code != 200:
            return _fail("real invoke returned non-200", payload={"status_code": response_real.status_code, "body": response_real.text})

        body_real = response_real.json()
        real_output = body_real.get("output")
        if not isinstance(real_output, str):
            return _fail("real invoke output must be a plain string", payload=body_real)

        if isinstance(sim_output, dict) and isinstance(real_output, str):
            print("[live_simulation_mode_invoke_check] PASS: simulation_mode passthrough works")
            print(
                json.dumps(
                    {
                        "base_url": base_url,
                        "sim_status": body_sim.get("status"),
                        "sim_output_kind": sim_output.get("kind"),
                        "real_status": body_real.get("status"),
                        "real_output_preview": real_output[:80],
                    },
                    ensure_ascii=True,
                )
            )
            return 0

        return _fail("unexpected mixed output types", payload={"sim": body_sim, "real": body_real})


if __name__ == "__main__":
    raise SystemExit(main())
