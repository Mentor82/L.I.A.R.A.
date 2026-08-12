"""
LIARA 20-Fragen-Benchmark
Sendet 20 Fragen an /chat, sammelt Antworten und erstellt eine Auswertung.
"""
import json
import time
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

BASE_URL = "http://127.0.0.1:8010"
SESSION_ID = f"benchmark-20q-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"

QUESTIONS = [
    # Weltgeschehen & Politik
    ("1", "EU CO₂ 2026", "Welche aktuellen Maßnahmen verfolgt die EU zur Reduzierung von CO₂-Emissionen im Jahr 2026?"),
    ("2", "Russland-Ukraine", "Wie entwickelt sich der Konflikt zwischen Russland und der Ukraine aktuell (Stand letzte 7 Tage)?"),
    ("3", "Neue Sanktionen", "Welche neuen Sanktionen wurden zuletzt international gegen ein Land verhängt und warum?"),
    ("4", "Wasserstoff-Politik DE", "Wie verändert sich aktuell die Energiepolitik in Deutschland im Hinblick auf Wasserstoff?"),
    ("5", "China KI", "Welche Rolle spielt China aktuell in der globalen KI-Entwicklung?"),
    # Technik & Innovation
    ("6", "Lokale LLMs 2026", "Welche neuen Entwicklungen gibt es bei lokalen LLMs (z. B. Ollama, llama.cpp) im Jahr 2026?"),
    ("7", "Edge-KI-Hardware", "Welche Fortschritte gibt es bei Edge-KI-Hardware (TPU, NPU, AI-Chips)?"),
    ("8", "Embedding-Modelle", "Wie unterscheiden sich aktuelle Embedding-Modelle hinsichtlich Dimensionen und Performance?"),
    ("9", "CVEs kürzlich", "Welche neuen Sicherheitslücken (CVEs) wurden kürzlich veröffentlicht?"),
    ("10", "Batteriespeicher", "Welche Trends gibt es aktuell bei Batteriespeichern oder Festkörperbatterien?"),
    # Fahrzeuge & Mobilität
    ("11", "EV 2026", "Welche neuen Entwicklungen gibt es bei Hybrid- oder Elektrofahrzeugen 2026?"),
    ("12", "Kraftstoffpreise DE", "Wie entwickeln sich die Preise für Kraftstoff (E10/Diesel) aktuell in Deutschland?"),
    ("13", "Auto-Rückrufe", "Welche technischen Probleme oder Rückrufe gab es kürzlich bei bekannten Automarken?"),
    # Wirtschaft & Märkte
    ("14", "Strompreise EU", "Wie entwickeln sich aktuell die Strompreise in Europa und warum?"),
    ("15", "Big Tech KI-Invest", "Welche großen Tech-Unternehmen investieren aktuell stark in KI und in welche Bereiche?"),
    ("16", "Bitcoin aktuell", "Wie steht es aktuell um Kryptowährungen wie Bitcoin?"),
    # Wissenschaft & Forschung
    ("17", "Fusionsforschung", "Welche neuen Erkenntnisse gibt es in der Fusionsforschung (z. B. ITER, private Projekte)?"),
    ("18", "Klimawandel-Studien", "Welche aktuellen Studien gibt es zu Klimawandel oder Extremwetter?"),
    # Meta / Bewertung
    ("19", "Quellen-Divergenz", "Welche Quellen berichten unterschiedlich über ein aktuelles politisches Ereignis – und warum?"),
    ("20", "Unsichere Infos", "Welche Informationen zum Thema Fusionsenergie sind wahrscheinlich unsicher oder widersprüchlich?"),
]

def chat(question: str, session_id: str, timeout: int = 90) -> dict:
    payload = json.dumps({
        "message": question,
        "session_id": session_id,
        "user_id": "benchmark",
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def grade(result: dict) -> dict:
    """Simple grading heuristics."""
    tools_used = result.get("tools_used") or []
    tool_outputs = result.get("tool_outputs") or {}
    response_text = result.get("response") or ""

    used_sys = "sys" in tools_used
    has_web_result = False
    kind = None
    has_knowledge_ref = "[KNOWLEDGE_REFERENCE]" in response_text
    answer_len = len(response_text.strip())

    for name, out in tool_outputs.items():
        if isinstance(out, dict):
            k = out.get("kind")
            if k in ("web_lookup", "release_lookup", "time_lookup"):
                has_web_result = True
                kind = k
            results = out.get("results") or []
            if results:
                has_web_result = True

    score = 0
    notes = []

    if used_sys:
        score += 2
        notes.append("sys genutzt")
    else:
        notes.append("⚠ kein sys-Tool")

    if has_web_result:
        score += 3
        notes.append(f"Web-Ergebnis ({kind})")
    else:
        notes.append("⚠ kein Web-Ergebnis")

    if has_knowledge_ref:
        score -= 1
        notes.append("⚠ [KNOWLEDGE_REFERENCE] in Antwort")

    if answer_len > 200:
        score += 1
        notes.append("ausführliche Antwort")
    elif answer_len < 50:
        score -= 1
        notes.append("⚠ sehr kurze Antwort")

    return {
        "score": score,
        "max": 6,
        "used_sys": used_sys,
        "has_web_result": has_web_result,
        "has_knowledge_ref": has_knowledge_ref,
        "kind": kind,
        "answer_len": answer_len,
        "notes": notes,
    }


def main():
    print(f"\n{'='*70}")
    print(f"  LIARA 20-Fragen-Benchmark  |  Session: {SESSION_ID}")
    print(f"  Start: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}\n")

    results = []
    total_time = 0.0

    for num, label, question in QUESTIONS:
        print(f"[{num:>2}/20] {label}")
        print(f"       Frage: {question[:80]}{'...' if len(question) > 80 else ''}")
        t0 = time.monotonic()
        error = None
        raw = {}
        try:
            raw = chat(question, SESSION_ID)
        except Exception as exc:
            error = str(exc)
        elapsed = time.monotonic() - t0
        total_time += elapsed

        if error:
            grade_result = {"score": 0, "max": 6, "notes": [f"FEHLER: {error}"],
                            "used_sys": False, "has_web_result": False,
                            "has_knowledge_ref": False, "kind": None, "answer_len": 0}
            response_excerpt = "(Fehler)"
        else:
            grade_result = grade(raw)
            response_text = raw.get("response") or ""
            response_excerpt = response_text[:200].replace("\n", " ")

        stars = "★" * grade_result["score"] + "☆" * (grade_result["max"] - grade_result["score"])
        print(f"       [{stars}] {grade_result['score']}/{grade_result['max']}  |  {elapsed:.1f}s")
        print(f"       → {', '.join(grade_result['notes'])}")
        if not error:
            print(f"       Antwort: {response_excerpt}")
        print()

        results.append({
            "num": num,
            "label": label,
            "question": question,
            "elapsed": elapsed,
            "error": error,
            "grade": grade_result,
            "response": (raw.get("response") or "") if not error else "",
            "tools_used": (raw.get("tools_used") or []) if not error else [],
            "tool_outputs": (raw.get("tool_outputs") or {}) if not error else {},
        })

    # ── Auswertung ──────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  AUSWERTUNG")
    print(f"{'='*70}")

    total_score = sum(r["grade"]["score"] for r in results)
    max_score = sum(r["grade"]["max"] for r in results)
    sys_count = sum(1 for r in results if r["grade"]["used_sys"])
    web_count = sum(1 for r in results if r["grade"]["has_web_result"])
    knowref_count = sum(1 for r in results if r["grade"]["has_knowledge_ref"])
    error_count = sum(1 for r in results if r["error"])
    avg_time = total_time / len(results)

    print(f"\n  Gesamt-Score:        {total_score} / {max_score}  ({100*total_score//max_score}%)")
    print(f"  sys-Tool genutzt:    {sys_count}/20 Fragen")
    print(f"  Web-Ergebnis:        {web_count}/20 Fragen")
    print(f"  [KNOWLEDGE_REFERENCE] in Antwort: {knowref_count}/20")
    print(f"  Fehler:              {error_count}/20")
    print(f"  Ø Antwortzeit:       {avg_time:.1f}s  (gesamt {total_time:.0f}s)")

    # Kategorien-Breakdown
    cats = {
        "Weltgeschehen & Politik (1-5)": results[0:5],
        "Technik & Innovation (6-10)":   results[5:10],
        "Fahrzeuge & Mobilität (11-13)": results[10:13],
        "Wirtschaft & Märkte (14-16)":   results[13:16],
        "Wissenschaft (17-18)":          results[16:18],
        "Meta / Bewertung (19-20)":      results[18:20],
    }
    print(f"\n  Kategorie-Breakdown:")
    for cat_name, cat_results in cats.items():
        cat_score = sum(r["grade"]["score"] for r in cat_results)
        cat_max   = sum(r["grade"]["max"] for r in cat_results)
        cat_web   = sum(1 for r in cat_results if r["grade"]["has_web_result"])
        cat_n     = len(cat_results)
        print(f"    {cat_name:<40} {cat_score:>3}/{cat_max}  Web: {cat_web}/{cat_n}")

    # Schwächste Fragen
    sorted_r = sorted(results, key=lambda r: r["grade"]["score"])
    print(f"\n  ↓ Schwächste 5 Fragen (niedrigster Score):")
    for r in sorted_r[:5]:
        print(f"    [{r['num']:>2}] {r['label']:<30} Score {r['grade']['score']}/{r['grade']['max']}  |  {', '.join(r['grade']['notes'])}")

    print(f"\n  ↑ Stärkste 5 Fragen:")
    for r in sorted_r[-1:-6:-1]:
        print(f"    [{r['num']:>2}] {r['label']:<30} Score {r['grade']['score']}/{r['grade']['max']}  |  {', '.join(r['grade']['notes'])}")

    # Qualitative Beobachtungen
    print(f"\n  Qualitative Beobachtungen:")
    print(f"  • DuckDuckGo-Suchergebnisse als Web-Quelle: {'vorhanden' if any(r['grade']['kind']=='web_lookup' for r in results) else 'keine'}")
    print(f"  • Release-Lookup genutzt: {'ja' if any(r['grade']['kind']=='release_lookup' for r in results) else 'nein'}")
    print(f"  • Fragen ohne Tool-Nutzung: {20 - sys_count} → wahrscheinlich aus Modellwissen beantwortet")
    kref_questions = [r['label'] for r in results if r['grade']['has_knowledge_ref']]
    if kref_questions:
        print(f"  • [KNOWLEDGE_REFERENCE] bei: {', '.join(kref_questions)}")

    print(f"\n{'='*70}\n")

    # JSON-Dump für Weiterverarbeitung
    out_path = f"logs/tests/benchmark_20q_{SESSION_ID}.json"
    import os; os.makedirs("logs/tests", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            "session_id": SESSION_ID,
            "timestamp": datetime.now(timezone.utc).isoformat(),
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
        }, f, ensure_ascii=False, indent=2)
    print(f"  JSON gespeichert: {out_path}\n")


if __name__ == "__main__":
    main()
