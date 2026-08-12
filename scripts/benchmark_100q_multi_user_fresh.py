"""LIARA 100-question multi-user live benchmark with fresh prompts.

Runs 100 chat/stream turns against the live API (default port 8010) using
10 distinct users and a fresh question set that does not reuse prompts from
existing benchmark scripts.

Outputs:
- JSONL: logs/tests/benchmark_100q_fresh_<timestamp>.jsonl
- JSON:  logs/tests/benchmark_100q_fresh_<timestamp>_summary.json
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
MAX_TOKENS = int(os.getenv("BENCHMARK_MAX_TOKENS", "512"))
TIMEOUT_S = int(os.getenv("BENCHMARK_TIMEOUT_S", "120"))

_TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
OUT_DIR = Path("logs") / "tests"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_JSONL = OUT_DIR / f"benchmark_100q_fresh_{_TS}.jsonl"
OUT_JSON = OUT_DIR / f"benchmark_100q_fresh_{_TS}_summary.json"

LATENCY_BUDGET = {"easy": 60.0, "medium": 60.0, "hard": 90.0}


@dataclass
class TurnSpec:
    idx: int
    user_id: str
    session_id: str
    topic: str
    difficulty: str
    question: str


@dataclass
class TurnResult:
    idx: int
    user_id: str
    session_id: str
    topic: str
    difficulty: str
    question: str
    elapsed_s: float
    response_excerpt: str
    progress_stages: list[str]
    tools_used: list[str]
    stream_complete: bool
    required_stages_ok: bool
    response_nonempty: bool
    latency_ok: bool
    error: str | None

    @property
    def passed(self) -> bool:
        return (
            self.stream_complete
            and self.required_stages_ok
            and self.response_nonempty
            and self.latency_ok
            and self.error is None
        )


def _configure_console_encoding() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _user_sessions() -> dict[str, str]:
    users = [
        "u_alex_smalltalk",
        "u_bianca_math",
        "u_cem_physics",
        "u_dina_python",
        "u_erik_data",
        "u_faye_systems",
        "u_gio_security",
        "u_hana_history",
        "u_ivan_productivity",
        "u_jule_creative",
    ]
    return {u: f"bench100-{u}-{uuid.uuid4().hex[:8]}" for u in users}


def _fresh_questions() -> list[tuple[str, str, str, str]]:
    return [
        # alex - smalltalk/general (10)
        ("u_alex_smalltalk", "smalltalk", "easy", "Nenne mir drei freundliche Eisbrecher-Fragen fuer ein erstes Teammeeting."),
        ("u_alex_smalltalk", "smalltalk", "easy", "Formuliere eine kurze, warmherzige Begruessung fuer einen neuen Kollegen am ersten Arbeitstag."),
        ("u_alex_smalltalk", "smalltalk", "medium", "Wie fuehre ich ein lockeres Gespraech, ohne aufdringlich zu wirken? Gib mir 5 konkrete Tipps."),
        ("u_alex_smalltalk", "smalltalk", "medium", "Welche Themen eignen sich fuer Smalltalk mit internationalem Publikum, welche sollte man eher meiden?"),
        ("u_alex_smalltalk", "smalltalk", "hard", "Entwerfe einen 15-Minuten-Ablauf fuer ein Remote-Kennenlernen mit introvertierten Teilnehmenden."),
        ("u_alex_smalltalk", "smalltalk", "easy", "Gib mir zwei humorvolle, aber respektvolle Antworten auf 'Wie laeuft's?' im Buero."),
        ("u_alex_smalltalk", "smalltalk", "medium", "Wie kann ich ein stockendes Gespraech elegant wieder in Gang bringen?"),
        ("u_alex_smalltalk", "smalltalk", "medium", "Schreibe drei Varianten fuer ein diplomatisches Thema-Wechseln im Gespraech."),
        ("u_alex_smalltalk", "smalltalk", "hard", "Vergleiche Smalltalk in DACH, UK und Japan: je 2 kulturelle Besonderheiten."),
        ("u_alex_smalltalk", "smalltalk", "easy", "Nenne mir 5 positive Abschluss-Saetze fuer ein kurzes Gespraech an der Kaffeemaschine."),

        # bianca - math (10)
        ("u_bianca_math", "math", "easy", "Loese 4x - 7 = 21 und erklaere jeden Schritt sehr kurz."),
        ("u_bianca_math", "math", "easy", "Berechne den Flaecheninhalt eines Rechtecks mit 8 cm und 13 cm."),
        ("u_bianca_math", "math", "medium", "Bestimme die Nullstellen von f(x)=x^2-5x+6."),
        ("u_bianca_math", "math", "medium", "Ein Kapital von 1200 Euro waechst 3 Jahre mit 4 Prozent p.a. (Zinseszins). Wie hoch ist der Endbetrag?"),
        ("u_bianca_math", "math", "hard", "Leite f(x)=x^3*e^x ab und vereinfache."),
        ("u_bianca_math", "math", "medium", "Eine Urne enthaelt 5 rote und 7 blaue Kugeln. Wie gross ist P(rot) bei einem Zug?"),
        ("u_bianca_math", "math", "hard", "Integriere f(x)=2x*cos(x)."),
        ("u_bianca_math", "math", "medium", "Loese das lineare Gleichungssystem: 2x+y=11 und x-y=1."),
        ("u_bianca_math", "math", "hard", "Gib eine kurze Herleitung, warum die Varianz nicht negativ sein kann."),
        ("u_bianca_math", "math", "easy", "Was ist der Unterschied zwischen Mittelwert und Median an einem einfachen Beispiel?"),

        # cem - physics (10)
        ("u_cem_physics", "physics", "easy", "Wie lautet die Formel fuer die Geschwindigkeit und was bedeuten die Variablen?"),
        ("u_cem_physics", "physics", "easy", "Ein Koerper legt 150 m in 12 s zurueck. Wie gross ist seine Durchschnittsgeschwindigkeit?"),
        ("u_cem_physics", "physics", "medium", "Erklaere den Unterschied zwischen kinetischer und potenzieller Energie mit je einem Alltagsbeispiel."),
        ("u_cem_physics", "physics", "medium", "Was passiert mit Stromstaerke und Leistung, wenn der Widerstand bei konstanter Spannung verdoppelt wird?"),
        ("u_cem_physics", "physics", "hard", "Leite aus den Newton-Gesetzen her, warum Impulserhaltung in einem abgeschlossenen System gilt."),
        ("u_cem_physics", "physics", "hard", "Erklaere den Treibhauseffekt physikalisch in 6-8 Saetzen ohne politische Wertung."),
        ("u_cem_physics", "physics", "medium", "Was bedeutet Resonanz in einem schwingungsfaehigen System? Nenne ein technisches Risiko."),
        ("u_cem_physics", "physics", "hard", "Vergleiche elektrische und magnetische Felder: Gemeinsamkeiten, Unterschiede, typische Anwendungen."),
        ("u_cem_physics", "physics", "easy", "Warum fuehlt sich Metall bei gleicher Raumtemperatur kaelter an als Holz?"),
        ("u_cem_physics", "physics", "medium", "Was ist der Unterschied zwischen Gleichstrom und Wechselstrom in der praktischen Versorgung?"),

        # dina - python (10)
        ("u_dina_python", "python", "easy", "Wofuer verwendet man in Python eine Liste und wofuer ein Set?"),
        ("u_dina_python", "python", "easy", "Zeige mir eine Python-Funktion, die prueft, ob eine Zahl gerade ist."),
        ("u_dina_python", "python", "medium", "Erklaere den Unterschied zwischen shallow copy und deep copy mit Mini-Beispiel."),
        ("u_dina_python", "python", "medium", "Wie funktioniert Fehlerbehandlung mit try/except/else/finally in Python?"),
        ("u_dina_python", "python", "hard", "Implementiere einen Decorator, der Laufzeit und Funktionsnamen loggt."),
        ("u_dina_python", "python", "hard", "Wann sind Dataclasses sinnvoller als klassische Klassen mit manuellem __init__?"),
        ("u_dina_python", "python", "medium", "Erklaere Iterator vs Generator praegnang und zeige je ein kurzes Beispiel."),
        ("u_dina_python", "python", "hard", "Wie vermeidet man Deadlocks bei mehreren Locks in Multi-Thread-Python-Code?"),
        ("u_dina_python", "python", "medium", "Gib ein Beispiel fuer Typ-Hinweise mit list[dict[str,int]] und erklaere den Nutzen."),
        ("u_dina_python", "python", "easy", "Warum sollte man mutable Default-Argumente in Python vermeiden?"),

        # erik - data/sql (10)
        ("u_erik_data", "data", "easy", "Was ist der praktische Unterschied zwischen CSV und Parquet?"),
        ("u_erik_data", "data", "medium", "Wann sollte man einen zusammengesetzten Index in SQL einsetzen?"),
        ("u_erik_data", "data", "easy", "Schreibe eine SQL-Abfrage, die pro Kunde die Anzahl Bestellungen zaehlt."),
        ("u_erik_data", "data", "medium", "Erklaere Window Functions am Beispiel ROW_NUMBER und PARTITION BY."),
        ("u_erik_data", "data", "hard", "Skizziere eine robuste ETL-Pipeline mit Idempotenz und Retry-Strategie."),
        ("u_erik_data", "data", "medium", "Was ist Data Drift und wie erkennt man sie in produktiven Pipelines?"),
        ("u_erik_data", "data", "hard", "Vergleiche Star-Schema und Snowflake-Schema fuer Analytics-Workloads."),
        ("u_erik_data", "data", "medium", "Wie kann man SQL-Queries systematisch auf Performance-Probleme untersuchen?"),
        ("u_erik_data", "data", "easy", "Was ist der Unterschied zwischen NULL und 0 in Datenbanken?"),
        ("u_erik_data", "data", "hard", "Erklaere Slowly Changing Dimensions (Typ 1, 2, 3) mit Use Cases."),

        # faye - systems/distributed (10)
        ("u_faye_systems", "systems", "easy", "Was ist der Unterschied zwischen horizontalem und vertikalem Skalieren?"),
        ("u_faye_systems", "systems", "medium", "Wozu dient ein Load Balancer und welche Strategien gibt es?"),
        ("u_faye_systems", "systems", "hard", "Erklaere CAP-Theorem mit einem praxisnahen Beispiel aus verteilten Datenbanken."),
        ("u_faye_systems", "systems", "medium", "Wie funktioniert ein Health-Check-Mechanismus fuer Microservices?"),
        ("u_faye_systems", "systems", "hard", "Skizziere ein Design fuer idempotente Event-Verarbeitung bei mindestens-einmal Zustellung."),
        ("u_faye_systems", "systems", "medium", "Was ist ein Circuit Breaker und wie verhindert er Kaskadenausfaelle?"),
        ("u_faye_systems", "systems", "hard", "Vergleiche Synchronous Request-Reply und Event-Driven Choreography im Domainenkontext."),
        ("u_faye_systems", "systems", "easy", "Warum braucht man Observability neben klassischem Logging?"),
        ("u_faye_systems", "systems", "medium", "Was ist der Unterschied zwischen RTO und RPO in Disaster Recovery?"),
        ("u_faye_systems", "systems", "hard", "Wie baut man Zero-Downtime-Deployments bei Schema-Migrationen?"),

        # gio - security (10)
        ("u_gio_security", "security", "easy", "Erklaere kurz den Unterschied zwischen Authentifizierung und Autorisierung."),
        ("u_gio_security", "security", "medium", "Welche Vorteile hat Multi-Faktor-Authentifizierung gegenueber reinem Passwortlogin?"),
        ("u_gio_security", "security", "medium", "Was ist SQL Injection und wie verhindert man sie in modernen Web-Backends?"),
        ("u_gio_security", "security", "hard", "Vergleiche JWT-basierte Session-Modelle mit serverseitigen Sessions inklusive Widerruf."),
        ("u_gio_security", "security", "hard", "Wie entwirft man ein Secrets-Management fuer CI/CD ohne Klartext in Pipelines?"),
        ("u_gio_security", "security", "medium", "Was bedeutet Least Privilege in Cloud-IAM und wie setzt man es praktisch um?"),
        ("u_gio_security", "security", "hard", "Beschreibe einen Incident-Response-Ablauf fuer einen vermuteten API-Key-Leak."),
        ("u_gio_security", "security", "easy", "Warum sind regelmaessige Security-Updates auch bei internen Tools wichtig?"),
        ("u_gio_security", "security", "medium", "Was ist der Unterschied zwischen Hashing und Verschluesselung?"),
        ("u_gio_security", "security", "hard", "Wie bewertet man Schwachstellen mit CVSS, und wo liegen Grenzen des Scores?"),

        # hana - history/society (10)
        ("u_hana_history", "history", "easy", "Was waren die wichtigsten Ausloeser der Aufklaerung in Europa?"),
        ("u_hana_history", "history", "medium", "Vergleiche kurz die Industrielle Revolution in Grossbritannien und Deutschland."),
        ("u_hana_history", "history", "medium", "Welche Rolle spielte die Druckerpresse fuer politische Umbrueche?"),
        ("u_hana_history", "history", "hard", "Analysiere Ursachen und Folgen der Weltwirtschaftskrise 1929 in drei Ebenen."),
        ("u_hana_history", "history", "easy", "Warum war der Hansebund fuer Staedte im Norden so bedeutsam?"),
        ("u_hana_history", "history", "hard", "Erklaere den Begriff 'Pfadabhaengigkeit' in der Geschichtswissenschaft mit Beispiel."),
        ("u_hana_history", "history", "medium", "Wie veraenderte die Elektrifizierung den Alltag zwischen 1880 und 1930?"),
        ("u_hana_history", "history", "hard", "Diskutiere Chancen und Risiken von Analogien zwischen antiken und modernen Imperien."),
        ("u_hana_history", "history", "easy", "Nenne drei historische Quellenarten und je eine Staerke/Schwaeche."),
        ("u_hana_history", "history", "medium", "Wie unterscheiden sich Primarquelle und Sekundarliteratur methodisch?"),

        # ivan - productivity/business (10)
        ("u_ivan_productivity", "productivity", "easy", "Nenne mir eine simple Tagesstruktur fuer fokussiertes Arbeiten in 4 Blöcken."),
        ("u_ivan_productivity", "productivity", "medium", "Wie priorisiert man Aufgaben mit Eisenhower-Matrix, ohne wichtige Themen zu vergessen?"),
        ("u_ivan_productivity", "productivity", "medium", "Welche Meeting-Regeln reduzieren unnoetige Dauer, ohne Informationsverlust?"),
        ("u_ivan_productivity", "productivity", "hard", "Erstelle ein Framework, um Teamziele in messbare Wochenziele herunterzubrechen."),
        ("u_ivan_productivity", "productivity", "easy", "Wie schreibt man eine gute To-do-Liste, die man tatsaechlich abarbeitet?"),
        ("u_ivan_productivity", "productivity", "hard", "Vergleiche OKR und KPI fuer ein Produktteam mit schnell wechselnden Prioritaeten."),
        ("u_ivan_productivity", "productivity", "medium", "Wie geht man mit Kontextwechselkosten in wissensintensiver Arbeit um?"),
        ("u_ivan_productivity", "productivity", "hard", "Skizziere ein Retrospektiven-Format, das bei verteilten Teams wirklich zu Entscheidungen fuehrt."),
        ("u_ivan_productivity", "productivity", "easy", "Nenne drei Signale, dass ein Sprintziel zu vage formuliert wurde."),
        ("u_ivan_productivity", "productivity", "medium", "Wie kann man asynchrone Kommunikation verbessern, damit weniger Nachfragen entstehen?"),

        # jule - creative/language (10)
        ("u_jule_creative", "creative", "easy", "Schreibe drei originelle Metaphern fuer 'Neustart' in einem positiven Ton."),
        ("u_jule_creative", "creative", "medium", "Gib mir ein Mini-Brainstorming mit 10 Ideen fuer einen Technik-Newsletter-Namen."),
        ("u_jule_creative", "creative", "medium", "Wie kann man einen trockenen Fachtext sprachlich lebendiger machen, ohne Praezision zu verlieren?"),
        ("u_jule_creative", "creative", "hard", "Entwerfe eine kurze Story-Struktur (Hook, Konflikt, Loesung) fuer ein Produkt-Launch-Video."),
        ("u_jule_creative", "creative", "easy", "Formuliere zwei freundliche Alternativen zu 'Das ist falsch'."),
        ("u_jule_creative", "creative", "medium", "Erklaere den Unterschied zwischen Tonalitaet, Stil und Register an je einem Satzbeispiel."),
        ("u_jule_creative", "creative", "hard", "Wie baut man in einer Rede Spannung auf, ohne Clickbait oder Uebertreibung?"),
        ("u_jule_creative", "creative", "easy", "Nenne fuenf kreative Schreibaufwaermungen fuer 10 Minuten."),
        ("u_jule_creative", "creative", "medium", "Wie prueft man systematisch, ob eine Erklaerung fuer Einsteiger wirklich verstaendlich ist?"),
        ("u_jule_creative", "creative", "hard", "Vergleiche drei didaktische Erklaerstile fuer komplexe Themen: analogisch, formal, problemorientiert."),
    ]


def _build_corpus() -> list[TurnSpec]:
    items = _fresh_questions()
    if len(items) != 100:
        raise RuntimeError(f"Expected exactly 100 questions, got {len(items)}")

    sessions = _user_sessions()
    turns: list[TurnSpec] = []
    for idx, (user_id, topic, difficulty, question) in enumerate(items, start=1):
        if user_id not in sessions:
            raise RuntimeError(f"Unknown user_id in corpus: {user_id}")
        if difficulty not in LATENCY_BUDGET:
            raise RuntimeError(f"Unknown difficulty in corpus: {difficulty}")
        turns.append(
            TurnSpec(
                idx=idx,
                user_id=user_id,
                session_id=sessions[user_id],
                topic=topic,
                difficulty=difficulty,
                question=question,
            )
        )
    return turns


def _payload(turn: TurnSpec) -> bytes:
    return json.dumps(
        {
            "session_id": turn.session_id,
            "user_id": turn.user_id,
            "message": turn.question,
            "max_tokens": MAX_TOKENS,
        }
    ).encode("utf-8")


def _read_sse_events(raw: bytes) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    current_event = ""
    for raw_line in raw.splitlines():
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
            continue
        if line.startswith("data:"):
            events.append((current_event, line.split(":", 1)[1].strip()))
    return events


def _call_stream(turn: TurnSpec) -> tuple[str, list[str], list[str], bool]:
    req = urllib.request.Request(
        f"{BASE_URL}/chat/stream",
        data=_payload(turn),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        raw = resp.read()

    events = _read_sse_events(raw)
    progress_stages: list[str] = []
    chunks: list[str] = []
    final_payload: dict[str, Any] = {}
    saw_done = False

    for event, data in events:
        try:
            obj = json.loads(data)
        except Exception:
            obj = {}

        if event == "progress":
            stage = str(obj.get("stage") or "")
            if stage:
                progress_stages.append(stage)
        elif event == "chunk":
            text = obj.get("text")
            if isinstance(text, str) and text:
                chunks.append(text)
        elif event == "final" and isinstance(obj, dict):
            final_payload = obj
        elif event == "done":
            saw_done = True

    response_text = "".join(chunks).strip() or str(final_payload.get("response") or "").strip()
    tools_used = final_payload.get("tools_used") or []
    stream_complete = bool(final_payload) and saw_done
    return response_text, progress_stages, tools_used, stream_complete


def _run_turn(turn: TurnSpec) -> TurnResult:
    started = time.monotonic()
    error: str | None = None
    response_text = ""
    progress_stages: list[str] = []
    tools_used: list[str] = []
    stream_complete = False

    try:
        response_text, progress_stages, tools_used, stream_complete = _call_stream(turn)
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, Exception) as exc:
        error = str(exc)

    elapsed = time.monotonic() - started
    required_stages_ok = "accepted" in progress_stages and "orchestration_complete" in progress_stages
    response_nonempty = bool(response_text.strip())
    latency_ok = elapsed <= LATENCY_BUDGET.get(turn.difficulty, 120.0)

    return TurnResult(
        idx=turn.idx,
        user_id=turn.user_id,
        session_id=turn.session_id,
        topic=turn.topic,
        difficulty=turn.difficulty,
        question=turn.question,
        elapsed_s=round(elapsed, 3),
        response_excerpt=response_text[:240].replace("\n", " "),
        progress_stages=progress_stages,
        tools_used=[str(t) for t in tools_used],
        stream_complete=stream_complete,
        required_stages_ok=required_stages_ok,
        response_nonempty=response_nonempty,
        latency_ok=latency_ok,
        error=error,
    )


def _write_jsonl_row(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _summary(rows: list[TurnResult]) -> dict[str, Any]:
    total = len(rows)
    passed = sum(1 for r in rows if r.passed)
    errors = sum(1 for r in rows if r.error is not None)
    stream_ok = sum(1 for r in rows if r.stream_complete)
    stages_ok = sum(1 for r in rows if r.required_stages_ok)
    nonempty_ok = sum(1 for r in rows if r.response_nonempty)
    latency_ok = sum(1 for r in rows if r.latency_ok)
    avg_s = round(sum(r.elapsed_s for r in rows) / total, 3) if total else 0.0

    by_topic: dict[str, int] = {}
    by_user: dict[str, int] = {}
    for r in rows:
        by_topic[r.topic] = by_topic.get(r.topic, 0) + 1
        by_user[r.user_id] = by_user.get(r.user_id, 0) + 1

    return {
        "base_url": BASE_URL,
        "total": total,
        "passed": passed,
        "pass_rate": round((passed / total) * 100.0, 2) if total else 0.0,
        "errors": errors,
        "stream_complete_ok": stream_ok,
        "required_stages_ok": stages_ok,
        "response_nonempty_ok": nonempty_ok,
        "latency_ok": latency_ok,
        "avg_latency_s": avg_s,
        "by_topic": by_topic,
        "by_user": by_user,
        "output_jsonl": str(OUT_JSONL),
    }


def main() -> None:
    _configure_console_encoding()
    turns = _build_corpus()

    print("=" * 84)
    print("LIARA Fresh 100Q Multi-User Benchmark")
    print(f"Base URL: {BASE_URL}")
    print(f"Total turns: {len(turns)}")
    print(f"Output JSONL: {OUT_JSONL}")
    print("=" * 84)

    rows: list[TurnResult] = []

    for turn in turns:
        result = _run_turn(turn)
        rows.append(result)
        _write_jsonl_row(OUT_JSONL, asdict(result))

        status = "OK" if result.passed else "FAIL"
        print(
            f"[{turn.idx:>3}/100] {status:<4} user={turn.user_id:<20} "
            f"topic={turn.topic:<12} diff={turn.difficulty:<6} "
            f"t={result.elapsed_s:>6}s err={'-' if not result.error else 'yes'}"
        )

    summary = _summary(rows)
    payload = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "summary": summary,
        "results": [asdict(r) for r in rows],
    }
    with OUT_JSON.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    print("\n" + "=" * 84)
    print("Summary")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Summary JSON: {OUT_JSON}")
    print("=" * 84)


if __name__ == "__main__":
    main()
