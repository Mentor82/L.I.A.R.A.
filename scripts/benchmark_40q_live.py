"""LIARA Live-Benchmark mit 40 Lernfragen."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010")
SESSION_ID = f"benchmark-40q-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

QUESTIONS = [
    ("1", "Compiler vs Interpreter", "Erklaere den Unterschied zwischen Compiler und Interpreter in 2-3 Saetzen."),
    ("2", "Statische Typisierung", "Was ist eine statische Typisierung, und nenne einen Vorteil."),
    ("3", "Dynamische Typisierung", "Was ist eine dynamische Typisierung, und nenne einen Vorteil."),
    ("4", "Null/None", "Warum sind Null-/None-Werte eine haeufige Fehlerquelle?"),
    ("5", "Big-O", "Was bedeutet Big-O-Notation bei Algorithmen?"),
    ("6", "Stack vs Heap", "Unterschied zwischen Stack und Heap?"),
    ("7", "Race Condition", "Was ist eine Race Condition?"),
    ("8", "Mutex", "Wozu dient ein Mutex?"),
    ("9", "Prozess vs Thread", "Unterschied zwischen Prozess und Thread?"),
    ("10", "Pure Funktion", "Was ist eine pure Funktion?"),
    ("11", "Ohmsches Gesetz", "Ohmsches Gesetz: Wie lautet die Formel?"),
    ("12", "Strom berechnen", "Berechne den Strom bei U=12V und R=6 Ohm."),
    ("13", "Leistung Formel", "Formel fuer elektrische Leistung?"),
    ("14", "Leistung berechnen", "Berechne die Leistung bei U=5V und I=2A."),
    ("15", "AC vs DC", "Unterschied zwischen AC und DC?"),
    ("16", "LED Vorwiderstand", "Warum braucht eine LED meist einen Vorwiderstand?"),
    ("17", "Diode", "Was macht eine Diode in Durchlassrichtung?"),
    ("18", "Zeitkonstante", "Was beschreibt die Zeitkonstante tau=RC?"),
    ("19", "Leitungswaerme", "Warum erwaermen sich Leitungen bei hohem Strom?"),
    ("20", "Wirkungsgrad", "Was bedeutet Wirkungsgrad in der Elektrotechnik?"),
    ("21", "v vs a", "Unterschied zwischen Geschwindigkeit und Beschleunigung?"),
    ("22", "Newton 2", "Newton 2: Wie lautet der Zusammenhang zwischen Kraft, Masse, Beschleunigung?"),
    ("23", "Impuls", "Was ist Impuls?"),
    ("24", "Impulserhaltung", "Was besagt Impulserhaltung?"),
    ("25", "Masse vs Gewichtskraft", "Was ist der Unterschied zwischen Masse und Gewichtskraft?"),
    ("26", "Arbeit", "Einheit der Arbeit und Bedeutung?"),
    ("27", "Waerme vs Temperatur", "Unterschied zwischen Waerme und Temperatur?"),
    ("28", "Freier Fall", "Warum faellt im Vakuum alles gleich schnell?"),
    ("29", "f und lambda", "Zusammenhang von Frequenz und Wellenlaenge bei konstanter Ausbreitungsgeschwindigkeit?"),
    ("30", "Resonanz", "Was ist Resonanz?"),
    ("31", "Gleichung 1", "Loese: 3x+5=20."),
    ("32", "Gleichung 2", "Loese: 2x-7=9."),
    ("33", "LGS Geometrie", "Was bedeutet eine Loesung eines linearen Gleichungssystems geometrisch?"),
    ("34", "LGS keine Loesung", "Wann hat ein LGS keine Loesung?"),
    ("35", "Mitternachtsformel", "Formel der Mitternachtsformel?"),
    ("36", "Diskriminante", "Rolle der Diskriminante b^2-4ac?"),
    ("37", "Determinante anschaulich", "Was ist eine Matrix-Determinante grob anschaulich?"),
    ("38", "Invertierbar", "Wann ist eine Matrix invertierbar?"),
    ("39", "Vektorraum", "Was ist ein Vektorraum (Kurzdefinition)?"),
    ("40", "Induktion", "Wozu dient vollstaendige Induktion?"),
]


def chat(question: str, session_id: str, timeout: int = 90) -> dict:
    # Each question gets its own isolated session to prevent context bleeding
    # between unrelated benchmark questions.
    isolated_sid = f"{session_id}-q{abs(hash(question)) % 100000:05d}"
    payload = json.dumps(
        {
            "message": question,
            "session_id": isolated_sid,
            "user_id": "benchmark",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def grade(result: dict) -> dict:
    tools_used = result.get("tools_used") or []
    tool_outputs = result.get("tool_outputs") or {}
    response_text = result.get("response") or ""

    used_sys = "sys" in tools_used
    has_web_result = False
    has_knowledge_ref = "[KNOWLEDGE_REFERENCE]" in response_text
    answer_len = len(response_text.strip())

    for out in tool_outputs.values():
        if isinstance(out, dict):
            results = out.get("results") or []
            if results:
                has_web_result = True

    score = 0
    notes = []

    if used_sys:
        score += 2
        notes.append("sys genutzt")
    else:
        notes.append("kein sys-Tool")

    if has_web_result:
        score += 2
        notes.append("Web-Ergebnis")
    else:
        notes.append("kein Web-Ergebnis")

    if has_knowledge_ref:
        score -= 1
        notes.append("KNOWLEDGE_REFERENCE")

    if answer_len > 120:
        score += 1
        notes.append("ausfuehrlich")
    elif answer_len < 40:
        score -= 1
        notes.append("sehr kurz")

    return {
        "score": score,
        "max": 5,
        "used_sys": used_sys,
        "has_web_result": has_web_result,
        "has_knowledge_ref": has_knowledge_ref,
        "answer_len": answer_len,
        "notes": notes,
    }


def _configure_console_encoding() -> None:
    """Avoid UnicodeEncodeError on Windows terminals using legacy code pages."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> None:
    _configure_console_encoding()
    print(f"\n{'='*70}")
    print(f"  LIARA 40-Fragen Live-Benchmark | Session: {SESSION_ID}")
    print(f"  Base URL: {BASE_URL}")
    print(f"{'='*70}\n")

    results = []
    total_time = 0.0

    for num, label, question in QUESTIONS:
        print(f"[{num:>2}/40] {label}")
        t0 = time.monotonic()
        error = None
        raw = {}
        try:
            raw = chat(question, SESSION_ID)
        except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, Exception) as exc:
            error = str(exc)
        elapsed = time.monotonic() - t0
        total_time += elapsed

        if error:
            grade_result = {
                "score": 0,
                "max": 5,
                "notes": [f"FEHLER: {error}"],
                "used_sys": False,
                "has_web_result": False,
                "has_knowledge_ref": False,
                "answer_len": 0,
            }
            response_excerpt = "(Fehler)"
        else:
            grade_result = grade(raw)
            response_text = raw.get("response") or ""
            response_excerpt = response_text[:160].replace("\n", " ")

        print(f"       Score {grade_result['score']}/{grade_result['max']}  |  {elapsed:.1f}s")
        print(f"       -> {', '.join(grade_result['notes'])}")
        print(f"       Antwort: {response_excerpt}\n")

        results.append(
            {
                "num": num,
                "label": label,
                "question": question,
                "elapsed": elapsed,
                "error": error,
                "grade": grade_result,
                "response": (raw.get("response") or "") if not error else "",
                "tools_used": (raw.get("tools_used") or []) if not error else [],
                "tool_outputs": (raw.get("tool_outputs") or {}) if not error else {},
            }
        )

    total_score = sum(r["grade"]["score"] for r in results)
    max_score = sum(r["grade"]["max"] for r in results)
    sys_count = sum(1 for r in results if r["grade"]["used_sys"])
    web_count = sum(1 for r in results if r["grade"]["has_web_result"])
    knowref_count = sum(1 for r in results if r["grade"]["has_knowledge_ref"])
    error_count = sum(1 for r in results if r["error"])
    avg_time = total_time / len(results)

    print(f"\n{'='*70}")
    print("  AUSWERTUNG")
    print(f"{'='*70}")
    print(f"Gesamt-Score: {total_score}/{max_score}")
    print(f"sys-Tool genutzt: {sys_count}/40")
    print(f"Web-Ergebnis: {web_count}/40")
    print(f"KNOWLEDGE_REFERENCE: {knowref_count}/40")
    print(f"Fehler: {error_count}/40")
    print(f"Durchschnittszeit: {avg_time:.1f}s")

    out_path = f"logs/tests/benchmark_40q_{SESSION_ID}.json"
    os.makedirs("logs/tests", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "session_id": SESSION_ID,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "base_url": BASE_URL,
                "summary": {
                    "total_score": total_score,
                    "max_score": max_score,
                    "sys_count": sys_count,
                    "web_count": web_count,
                    "knowref_count": knowref_count,
                    "error_count": error_count,
                    "avg_time_s": round(avg_time, 2),
                },
                "results": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"JSON gespeichert: {out_path}")


if __name__ == "__main__":
    main()
