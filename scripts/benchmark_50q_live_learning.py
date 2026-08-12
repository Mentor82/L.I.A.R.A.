"""LIARA Live-Benchmark mit 50 Fragen und Lerneffekt.

Ablauf:
- 25 Speicherfragen (Fakten einpraegen)
- 25 Abruffragen (Fakten wiedergeben)

Der Benchmark nutzt /chat/stream im selben session_id-Kontext, um den
Memory-Effekt ueber progress-events (memory_effect_detected) und
Antwortinhalt zu bewerten.

Hinweis:
- Dieses Skript fuehrt den Benchmark aus, wenn es gestartet wird.
- Die Auswertung wird als JSON unter logs/tests gespeichert.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
SESSION_ID = os.getenv(
    "LIARA_BENCHMARK_SESSION_ID",
    f"benchmark-50q-learning-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}",
)
USER_ID = os.getenv("LIARA_BENCHMARK_USER_ID", "benchmark-learning")
MAX_TOKENS = int(os.getenv("LIARA_BENCHMARK_MAX_TOKENS", "512"))
TIMEOUT_S = int(os.getenv("LIARA_BENCHMARK_TIMEOUT_S", "180"))


@dataclass(frozen=True)
class FactPair:
    key: str
    value: str


FACTS: list[FactPair] = [
    FactPair("lieblingsfarbe", "tuerkis"),
    FactPair("stadt", "koeln"),
    FactPair("haustier", "beagle"),
    FactPair("programmiersprache", "python"),
    FactPair("lieblingsgetraenk", "ingwertee"),
    FactPair("hobby", "klettern"),
    FactPair("geburtsmonat", "maerz"),
    FactPair("arbeitsmodus", "fokus"),
    FactPair("lieblingsessen", "lasagne"),
    FactPair("projektname", "liara"),
    FactPair("alias", "mira"),
    FactPair("lieblingszahl", "17"),
    FactPair("uhrzeitfenster", "fruehmorgen"),
    FactPair("reiseziel", "oslo"),
    FactPair("musikgenre", "jazz"),
    FactPair("favoritensport", "badminton"),
    FactPair("notizfarbe", "gelb"),
    FactPair("arbeitsort", "homeoffice"),
    FactPair("lernziel", "systemdesign"),
    FactPair("bevorzugtes_os", "linux"),
    FactPair("editor", "vscode"),
    FactPair("datenbank", "postgres"),
    FactPair("meetingtag", "dienstag"),
    FactPair("snack", "mandeln"),
    FactPair("abschlusswort", "verstanden"),
]


def _configure_console_encoding() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _make_stream_request(message: str) -> urllib.request.Request:
    payload = json.dumps(
        {
            "session_id": SESSION_ID,
            "user_id": USER_ID,
            "message": message,
            "max_tokens": MAX_TOKENS,
        }
    ).encode("utf-8")
    return urllib.request.Request(
        f"{BASE_URL}/chat/stream",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )


def _read_sse_events(resp_bytes: bytes) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    current_event = ""
    for raw_line in resp_bytes.splitlines():
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            events.append((current_event, line.split(":", 1)[1].strip()))
    return events


def chat_stream(message: str, timeout: int = TIMEOUT_S) -> dict:
    req = _make_stream_request(message)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()

    events = _read_sse_events(body)

    progress_stages: list[str] = []
    memory_effect_detected = False
    response_chunks: list[str] = []
    final_payload: dict = {}

    for event, data in events:
        try:
            obj = json.loads(data)
        except Exception:
            obj = {}

        if event == "progress":
            stage = str(obj.get("stage") or "")
            if stage:
                progress_stages.append(stage)
            if stage == "memory_effect_detected":
                memory_effect_detected = True

        elif event == "chunk":
            text = obj.get("text")
            if isinstance(text, str) and text:
                response_chunks.append(text)

        elif event == "final" and isinstance(obj, dict):
            final_payload = obj

    response_text = "".join(response_chunks).strip()
    if not response_text:
        response_text = str(final_payload.get("response") or "").strip()

    return {
        "events": events,
        "progress_stages": progress_stages,
        "memory_effect_detected": memory_effect_detected,
        "response_text": response_text,
        "final_payload": final_payload,
    }


def _store_prompt(fact: FactPair) -> str:
    return (
        f"Merke dir bitte fuer spaeter: Meine {fact.key} ist {fact.value}. "
        "Bestaetige kurz mit ok gespeichert."
    )


def _recall_prompt(fact: FactPair) -> str:
    return (
        f"Welche {fact.key} habe ich dir genannt? "
        "Antworte nur mit dem Wert."
    )


def _hit_expected(response: str, expected: str) -> bool:
    return expected.lower() in response.lower()


def run_benchmark() -> dict:
    rows: list[dict] = []
    total_time = 0.0

    turn = 0
    for fact in FACTS:
        turn += 1
        t0 = time.monotonic()
        err = None
        result: dict = {}
        prompt = _store_prompt(fact)
        try:
            result = chat_stream(prompt)
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, Exception) as exc:
            err = str(exc)
        elapsed = time.monotonic() - t0
        total_time += elapsed

        rows.append(
            {
                "turn": turn,
                "phase": "store",
                "key": fact.key,
                "expected": fact.value,
                "prompt": prompt,
                "elapsed_s": round(elapsed, 3),
                "error": err,
                "memory_effect_detected": bool(result.get("memory_effect_detected")) if not err else False,
                "progress_stages": result.get("progress_stages") if not err else [],
                "response": result.get("response_text", "") if not err else "",
                "expected_hit": None,
                "score": 0 if err else 1,
            }
        )

    for fact in FACTS:
        turn += 1
        t0 = time.monotonic()
        err = None
        result = {}
        prompt = _recall_prompt(fact)
        try:
            result = chat_stream(prompt)
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, Exception) as exc:
            err = str(exc)
        elapsed = time.monotonic() - t0
        total_time += elapsed

        response_text = result.get("response_text", "") if not err else ""
        expected_hit = _hit_expected(response_text, fact.value) if not err else False
        memory_flag = bool(result.get("memory_effect_detected")) if not err else False

        score = 0
        if not err:
            if memory_flag:
                score += 1
            if expected_hit:
                score += 2

        rows.append(
            {
                "turn": turn,
                "phase": "recall",
                "key": fact.key,
                "expected": fact.value,
                "prompt": prompt,
                "elapsed_s": round(elapsed, 3),
                "error": err,
                "memory_effect_detected": memory_flag,
                "progress_stages": result.get("progress_stages") if not err else [],
                "response": response_text,
                "expected_hit": expected_hit,
                "score": score,
            }
        )

        print(
            f"[{turn:>2}/50] recall {fact.key:<16} "
            f"score={score}/3 mem={str(memory_flag).lower()} hit={str(expected_hit).lower()} "
            f"t={elapsed:.1f}s"
        )

    recall_rows = [r for r in rows if r["phase"] == "recall"]
    store_rows = [r for r in rows if r["phase"] == "store"]

    errors = sum(1 for r in rows if r["error"])
    recall_memory_hits = sum(1 for r in recall_rows if r["memory_effect_detected"])
    recall_expected_hits = sum(1 for r in recall_rows if r["expected_hit"])
    recall_full_hits = sum(
        1 for r in recall_rows if r["memory_effect_detected"] and r["expected_hit"]
    )

    total_score = sum(r["score"] for r in rows)
    max_score = len(store_rows) * 1 + len(recall_rows) * 3

    summary = {
        "turns_total": len(rows),
        "store_turns": len(store_rows),
        "recall_turns": len(recall_rows),
        "errors": errors,
        "total_score": total_score,
        "max_score": max_score,
        "score_pct": round((100.0 * total_score / max_score) if max_score else 0.0, 2),
        "recall_memory_effect_detected": recall_memory_hits,
        "recall_expected_hit": recall_expected_hits,
        "recall_full_hit": recall_full_hits,
        "avg_turn_time_s": round(total_time / len(rows), 3) if rows else 0.0,
        "total_time_s": round(total_time, 2),
    }

    return {
        "session_id": SESSION_ID,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "user_id": USER_ID,
        "max_tokens": MAX_TOKENS,
        "summary": summary,
        "results": rows,
    }


def main() -> None:
    _configure_console_encoding()

    print("\n" + "=" * 72)
    print(f"  LIARA 50-Fragen Live-Learning-Benchmark | Session: {SESSION_ID}")
    print(f"  Base URL: {BASE_URL}")
    print("=" * 72 + "\n")

    report = run_benchmark()
    summary = report["summary"]

    print("\n" + "=" * 72)
    print("  AUSWERTUNG (KURZ)")
    print("=" * 72)
    print(f"Turns gesamt:                {summary['turns_total']}")
    print(f"Store-Turns:                 {summary['store_turns']}")
    print(f"Recall-Turns:                {summary['recall_turns']}")
    print(f"Fehler gesamt:               {summary['errors']}")
    print(f"Gesamt-Score:                {summary['total_score']}/{summary['max_score']}")
    print(f"Score in %:                  {summary['score_pct']}%")
    print(f"Recall memory_effect_detected:{summary['recall_memory_effect_detected']}/25")
    print(f"Recall expected_hit:         {summary['recall_expected_hit']}/25")
    print(f"Recall full_hit:             {summary['recall_full_hit']}/25")
    print(f"Durchschnitt Turn-Zeit:      {summary['avg_turn_time_s']}s")
    print(f"Gesamtdauer:                 {summary['total_time_s']}s")

    out_path = f"logs/tests/benchmark_50q_learning_{SESSION_ID}.json"
    os.makedirs("logs/tests", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\nJSON gespeichert: {out_path}\n")


if __name__ == "__main__":
    main()
