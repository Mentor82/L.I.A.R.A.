import asyncio
import csv
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path

import httpx

from services.config.settings import Settings
from services.inference.llama_cpp_server import LlamaCppServerManager

QUESTION = """Ein Kondensator C = 1mF ist an eine Wechselspannungsquelle u(t) = 10V⋅sin(2πft) angeschlossen.
Die Frequenz der Wechselspannung beträagt f = 50Hz. Es fließt derStrom i(t) = 1A⋅π⋅cos(2πft).

- Stellen Sie die zeitlichen Verläufe von u(t), i(t) und der Leistung p(t) (= Produkt aus Spannung und Strom) in einem gemeinsamen Diagramm grafisch dar.
- Zeichnen die Spannung in blauer, den Strom in roter Farbe. Der Verlauf der Leistung soll in grüner Farbe gestrichelt dargestellt werden.
- Füge den Titel "Spannung, Strom und Leistung" zum Diagramm hinzu.
- Füge Achsenbeschriftungen und eine Legende zum Diagramm hinzu.
"""

VARIANTS = [
    "sycl-fp16-intel-arc",
    "vulkan-cross-gpu",
    "cpu-avx2-f16c",
]


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _port_from_url(url: str) -> int:
    try:
        return int(url.rsplit(":", 1)[-1])
    except Exception:
        return 8091


def _kill_llama_server_processes() -> None:
    # For benchmark stability, we force-kill any leftover llama-server process.
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/IM", "llama-server.exe", "/F"],
            capture_output=True,
            text=False,
            check=False,
        )
    else:
        subprocess.run(["pkill", "-f", "llama-server"], capture_output=True, text=False, check=False)


def _kill_processes_on_port(port: int) -> None:
    if os.name != "nt":
        return

    out = subprocess.run(
        ["netstat", "-ano", "-p", "tcp"],
        capture_output=True,
        text=False,
        check=False,
    )
    if out.returncode != 0:
        return

    pids: set[str] = set()
    marker = f":{port}"
    for raw_line in out.stdout.splitlines():
        line = raw_line.decode("utf-8", errors="ignore").strip()
        if marker not in line:
            continue
        parts = line.split()
        if len(parts) >= 5:
            pid = parts[-1].strip()
            if pid.isdigit() and pid != "0":
                pids.add(pid)

    for pid in pids:
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True, text=False, check=False)


async def _cleanup_before_run(base_url: str) -> None:
    port = _port_from_url(base_url)
    _kill_processes_on_port(port)
    _kill_llama_server_processes()

    # Give drivers/runtime time to release model/GPU resources.
    await asyncio.sleep(4)

    # Confirm benchmark port no longer responds.
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            await client.get(f"{base_url}/health")
        # If still alive, wait a little longer before trying next variant.
        await asyncio.sleep(3)
    except Exception:
        pass


async def run_one_variant(variant: str, base_url: str) -> dict:
    await _cleanup_before_run(base_url)

    manager = LlamaCppServerManager(
        base_url=base_url,
        model_path=Settings.LLAMA_CPP_MODEL,
        timeout_seconds=max(240, int(Settings.LLAMA_CPP_TIMEOUT_SECONDS)),
        build_variant=variant,
    )

    result: dict = {
        "variant": variant,
        "base_url": base_url,
        "model": Settings.LLAMA_CPP_MODEL,
        "ok": False,
        "startup_ms": None,
        "request_ms": None,
        "total_ms": None,
        "status_code": None,
        "error": None,
        "response_chars": 0,
        "finish_reason": None,
        "usage": {},
    }

    total_started = time.perf_counter()

    startup_started = time.perf_counter()
    started = await manager.start(verbose=True)
    result["startup_ms"] = round((time.perf_counter() - startup_started) * 1000, 2)

    if not started:
        result["error"] = "llama-server start failed"
        result["total_ms"] = round((time.perf_counter() - total_started) * 1000, 2)
        return result

    try:
        payload = {
            "model": Settings.LLAMA_CPP_MODEL,
            "messages": [{"role": "user", "content": QUESTION}],
            "temperature": 0.2,
            "max_tokens": 700,
            "stream": False,
        }

        req_started = time.perf_counter()
        async with httpx.AsyncClient(timeout=max(300, int(Settings.LLAMA_CPP_TIMEOUT_SECONDS))) as client:
            resp = await client.post(f"{base_url}/v1/chat/completions", json=payload)
        result["request_ms"] = round((time.perf_counter() - req_started) * 1000, 2)
        result["status_code"] = resp.status_code

        if resp.status_code != 200:
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:400]}"
            result["total_ms"] = round((time.perf_counter() - total_started) * 1000, 2)
            return result

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = str(message.get("content") or "")

        result["ok"] = True
        result["response_chars"] = len(content)
        result["finish_reason"] = choice.get("finish_reason")
        result["usage"] = data.get("usage") or {}
        result["total_ms"] = round((time.perf_counter() - total_started) * 1000, 2)
        return result

    except Exception as exc:
        result["error"] = str(exc)
        result["total_ms"] = round((time.perf_counter() - total_started) * 1000, 2)
        return result
    finally:
        await manager.stop(verbose=False)
        await asyncio.sleep(3)


async def main() -> None:
    base_url = "http://127.0.0.1:8091"
    started_at = datetime.now().isoformat()

    rows: list[dict] = []
    for variant in VARIANTS:
        print(f"[RUN] variant={variant}")
        row = await run_one_variant(variant, base_url=base_url)
        rows.append(row)
        print(
            "[DONE]",
            variant,
            "ok=", row["ok"],
            "startup_ms=", row["startup_ms"],
            "request_ms=", row["request_ms"],
            "total_ms=", row["total_ms"],
            "error=", row["error"],
        )

    finished_at = datetime.now().isoformat()

    out_dir = Path("c:/ai/LIARA/logs/tests")
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_stamp()

    json_path = out_dir / f"llama_variant_timing_{stamp}.json"
    csv_path = out_dir / f"llama_variant_timing_{stamp}.csv"

    payload = {
        "started_at": started_at,
        "finished_at": finished_at,
        "question": QUESTION,
        "variants": VARIANTS,
        "results": rows,
    }

    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "variant",
                "ok",
                "startup_ms",
                "request_ms",
                "total_ms",
                "status_code",
                "response_chars",
                "finish_reason",
                "error",
            ],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(
                {
                    "variant": r.get("variant"),
                    "ok": r.get("ok"),
                    "startup_ms": r.get("startup_ms"),
                    "request_ms": r.get("request_ms"),
                    "total_ms": r.get("total_ms"),
                    "status_code": r.get("status_code"),
                    "response_chars": r.get("response_chars"),
                    "finish_reason": r.get("finish_reason"),
                    "error": r.get("error"),
                }
            )

    print("[LOG] json=", json_path)
    print("[LOG] csv=", csv_path)


if __name__ == "__main__":
    asyncio.run(main())
