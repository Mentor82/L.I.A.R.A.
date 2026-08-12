from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def _fetch_health(base_url: str, timeout: float) -> dict:
    request = urllib.request.Request(f"{base_url.rstrip('/')}/health", method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Run embedding runtime matrix health checks.")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds")
    args = parser.parse_args()

    scenarios = {
        "npu_openvino": os.getenv("EMBEDDING_MATRIX_NPU_URL", os.getenv("EMBEDDING_SERVICE_BASE_URL", "http://127.0.0.1:8030")),
        "cpu_fallback": os.getenv("EMBEDDING_MATRIX_CPU_FALLBACK_URL", ""),
        "compose_cpu": os.getenv("EMBEDDING_MATRIX_COMPOSE_CPU_URL", ""),
    }

    overall_ok = True
    for name, url in scenarios.items():
        if not url:
            print(f"MATRIX_SKIP {name}: no URL configured")
            continue
        try:
            payload = _fetch_health(url, timeout=args.timeout)
            status = (((payload or {}).get("status") or {}).get("status") or "unknown")
            metadata = (((payload or {}).get("status") or {}).get("metadata") or {})
            runtime_backend = metadata.get("runtime_backend") or payload.get("runtime_backend")
            device = metadata.get("device") or payload.get("device")
            alerts = ((metadata.get("alerts") or {}).get("active") or [])
            print(f"MATRIX_OK {name}: status={status} backend={runtime_backend} device={device} alerts={alerts}")
            if status not in {"success", "partial"}:
                overall_ok = False
        except urllib.error.URLError as exc:
            overall_ok = False
            print(f"MATRIX_FAIL {name}: {exc}")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
