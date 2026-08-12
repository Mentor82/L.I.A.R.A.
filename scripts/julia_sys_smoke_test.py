"""Smoke test for the unified Julia execution path via WSL /sys."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.simulation.bridge import JuliaBridge, JuliaBridgeError


async def _main() -> int:
    bridge = JuliaBridge(timeout_seconds=30.0)
    try:
        result = await bridge.run(
            "turbine_power",
            {"shaft_speed_rpm": 1500.0, "torque_nm": 200.0},
        )
    except JuliaBridgeError as exc:
        print(f"SMOKE_FAIL: {exc}")
        return 1

    power_kw = float(result.get("power_kw", -1))
    if abs(power_kw - 31.4159) > 0.02:
        print(f"SMOKE_FAIL: unexpected power_kw={power_kw}")
        return 1

    print(json.dumps({"status": "ok", "result": result}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))