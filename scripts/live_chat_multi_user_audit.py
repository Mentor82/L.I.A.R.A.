"""Live chat audit with multiple users and varied task types.

Scenarios:
1) Memory user: stores personal facts and verifies recall.
2) Compute user: asks for a Python calculation.
3) Planning user: asks for multi-option planning output.

Writes a JSON report and exits non-zero when required checks fail.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass

import httpx


BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
TIMEOUT_SECONDS = float(os.getenv("LIVE_CHAT_AUDIT_TIMEOUT_SECONDS", "180"))
AUDIT_DIR = os.getenv("AUDIT_DIR", "")


@dataclass
class ScenarioResult:
    name: str
    user_id: str
    session_id: str
    passed: bool
    checks: list[str]
    failures: list[str]
    observed_stages: list[str]
    response_excerpt: str
    elapsed_ms: float


def _read_sse_events(response: httpx.Response) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    current_event = ""
    for line in response.iter_lines():
        if line is None:
            continue
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        if not text:
            continue
        if text.startswith("event:"):
            current_event = text.split(":", 1)[1].strip()
            continue
        if text.startswith("data:"):
            events.append((current_event, text.split(":", 1)[1].strip()))
    return events


def _stream_turn(
    client: httpx.Client,
    *,
    session_id: str,
    user_id: str,
    message: str,
    max_tokens: int = 512,
) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "message": message,
        "max_tokens": max_tokens,
    }
    with client.stream("POST", "/chat/stream", json=payload) as response:
        response.raise_for_status()
        events = _read_sse_events(response)

    progress = [json.loads(data) for event, data in events if event == "progress"]
    chunks = [json.loads(data) for event, data in events if event == "chunk"]
    final = [json.loads(data) for event, data in events if event == "final"]
    done = [data for event, data in events if event == "done"]
    return progress, chunks, final, done


def _run_memory_scenario(client: httpx.Client) -> ScenarioResult:
    user_id = "audit-memory-user"
    session_id = f"audit-memory-{uuid.uuid4().hex[:8]}"
    checks: list[str] = []
    failures: list[str] = []

    t0 = time.perf_counter()
    p1, c1, f1, d1 = _stream_turn(
        client,
        session_id=session_id,
        user_id=user_id,
        message="Bitte merke dir: Ich heisse Mira und wohne in Ulm.",
        max_tokens=220,
    )
    p2, c2, f2, d2 = _stream_turn(
        client,
        session_id=session_id,
        user_id=user_id,
        message="Wie heisse ich und wo wohne ich?",
        max_tokens=220,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    stages = [x.get("stage") for x in p2]
    text2 = "".join(x.get("text", "") for x in c2).lower()

    if p1 and c1 and f1 and d1:
        checks.append("turn1_stream_complete")
    else:
        failures.append("turn1_stream_incomplete")

    if "accepted" in stages and "orchestration_complete" in stages:
        checks.append("turn2_required_stages")
    else:
        failures.append(f"turn2_missing_stages:{stages}")

    if "mira" in text2 and "ulm" in text2:
        checks.append("turn2_recall_name_and_city")
    else:
        failures.append("turn2_missing_memory_recall")

    if f2 and d2:
        checks.append("turn2_final_and_done")
    else:
        failures.append("turn2_no_final_or_done")

    return ScenarioResult(
        name="memory_recall",
        user_id=user_id,
        session_id=session_id,
        passed=not failures,
        checks=checks,
        failures=failures,
        observed_stages=[s for s in stages if s],
        response_excerpt=text2[:280],
        elapsed_ms=elapsed_ms,
    )


def _run_compute_scenario(client: httpx.Client) -> ScenarioResult:
    user_id = "audit-compute-user"
    session_id = f"audit-compute-{uuid.uuid4().hex[:8]}"
    checks: list[str] = []
    failures: list[str] = []

    t0 = time.perf_counter()
    p, c, f, d = _stream_turn(
        client,
        session_id=session_id,
        user_id=user_id,
        message="Berechne 12 * 17 mit Python und gib nur Ergebnis plus einen Satz aus.",
        max_tokens=220,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    stages = [x.get("stage") for x in p]
    text = "".join(x.get("text", "") for x in c)

    if "accepted" in stages and "orchestration_complete" in stages:
        checks.append("required_stages")
    else:
        failures.append(f"missing_stages:{stages}")

    if len(text.strip()) > 0:
        checks.append("non_empty_response")
    else:
        failures.append("empty_response")

    if "204" in text or "12" in text or "17" in text:
        checks.append("compute_signal_in_text")
    else:
        failures.append("missing_compute_signal")

    if f and d:
        checks.append("final_and_done")
    else:
        failures.append("no_final_or_done")

    return ScenarioResult(
        name="compute_python",
        user_id=user_id,
        session_id=session_id,
        passed=not failures,
        checks=checks,
        failures=failures,
        observed_stages=[s for s in stages if s],
        response_excerpt=text[:280],
        elapsed_ms=elapsed_ms,
    )


def _run_planning_scenario(client: httpx.Client) -> ScenarioResult:
    user_id = "audit-planning-user"
    session_id = f"audit-planning-{uuid.uuid4().hex[:8]}"
    checks: list[str] = []
    failures: list[str] = []

    t0 = time.perf_counter()
    p, c, f, d = _stream_turn(
        client,
        session_id=session_id,
        user_id=user_id,
        message=(
            "Plane 3 Optionen fuer Solarpanels mit 40, 70 und 100 Prozent Budgetnutzung. "
            "Ein Panel kostet 190 Euro und hat 420 Watt peak. Gib eine kompakte Tabelle."
        ),
        max_tokens=320,
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    stages = [x.get("stage") for x in p]
    text = "".join(x.get("text", "") for x in c)
    lower = text.lower()

    if "accepted" in stages and "orchestration_complete" in stages:
        checks.append("required_stages")
    else:
        failures.append(f"missing_stages:{stages}")

    if any(token in text for token in ["40", "70", "100"]):
        checks.append("contains_option_markers")
    else:
        failures.append("missing_option_markers")

    if any(token in lower for token in ["panel", "budget", "euro", "watt", "tabelle"]):
        checks.append("contains_domain_terms")
    else:
        failures.append("missing_domain_terms")

    if f and d:
        checks.append("final_and_done")
    else:
        failures.append("no_final_or_done")

    return ScenarioResult(
        name="planning_multi_option",
        user_id=user_id,
        session_id=session_id,
        passed=not failures,
        checks=checks,
        failures=failures,
        observed_stages=[s for s in stages if s],
        response_excerpt=text[:280],
        elapsed_ms=elapsed_ms,
    )


def main() -> int:
    started = time.strftime("%Y-%m-%dT%H:%M:%S")
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT_SECONDS) as client:
        results = [
            _run_memory_scenario(client),
            _run_compute_scenario(client),
            _run_planning_scenario(client),
        ]

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    payload = {
        "audit": "live_chat_multi_user",
        "started_at": started,
        "base_url": BASE_URL,
        "timeout_seconds": TIMEOUT_SECONDS,
        "summary": {
            "scenario_count": len(results),
            "passed": passed,
            "failed": failed,
        },
        "results": [asdict(r) for r in results],
    }

    print(json.dumps(payload, ensure_ascii=True, indent=2))

    if AUDIT_DIR:
        report_path = os.path.join(AUDIT_DIR, "09_live_chat_multi_user_audit.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=True, indent=2)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
