"""LIARA 1000-Fragen Chat-Flow Benchmark mit Lerneffekten (Grundschule bis 5. Klasse).

Ziel:
- 1000 Turns insgesamt
- Deutsch + Englisch
- Typische Schulfaecher (Klasse 1-5)
- Lerneffekte ueber Memory-Store/Recall im selben Session-Kontext

Ausgabe:
- JSONL:   logs/tests/benchmark_1000q_primary_<ts>.jsonl
- Summary: logs/tests/benchmark_1000q_primary_<ts>_summary.json

Umgebungsvariablen:
  LIARA_API_BASE_URL           Ziel-API (default http://127.0.0.1:8010)
  BENCHMARK_MAX_TOKENS         Max-Tokens pro Antwort (default 512)
  BENCHMARK_TIMEOUT_S          Request-Timeout in Sekunden (default 120)
  BENCHMARK_AUDIT_DIR          Override Ausgabeverzeichnis
  BENCHMARK_USER_FILTER        Komma-getrennte user_id-Praefix-Filter
  BENCHMARK_DRY_RUN            1 = kein HTTP, nur Corpus/Audit-Simulation
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional
import urllib.request


BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
MAX_TOKENS = int(os.getenv("BENCHMARK_MAX_TOKENS", "512"))
TIMEOUT_S = int(os.getenv("BENCHMARK_TIMEOUT_S", "120"))
USER_FILTER = [u.strip() for u in os.getenv("BENCHMARK_USER_FILTER", "").split(",") if u.strip()]
DRY_RUN = os.getenv("BENCHMARK_DRY_RUN", "0") == "1"

_TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
_DEFAULT_AUDIT_DIR = os.path.join(os.path.dirname(__file__), "..", "logs", "tests")
AUDIT_DIR = os.getenv("BENCHMARK_AUDIT_DIR", _DEFAULT_AUDIT_DIR)

LATENCY_BUDGET = {"easy": 45.0, "medium": 60.0, "hard": 90.0}
GRADES = [1, 2, 3, 4, 5]


@dataclass
class TurnSpec:
    user_id: str
    language: str          # de | en
    grade: int             # 1..5
    subject: str
    message: str
    topic: str
    difficulty: str        # easy | medium | hard
    burst: int
    expect_keywords: list[str] = field(default_factory=list)


@dataclass
class TurnResult:
    turn_index: int
    user_id: str
    language: str
    grade: int
    subject: str
    session_id: str
    message: str
    topic: str
    difficulty: str
    burst: int
    elapsed_s: float
    response_excerpt: str
    progress_stages: list[str]
    memory_effect_detected: bool
    tools_used: list[str]
    stream_complete: bool
    required_stages_ok: bool
    response_nonempty: bool
    latency_ok: bool
    recall_ok: bool
    has_recall_check: bool
    error: Optional[str]

    @property
    def passed(self) -> bool:
        return (
            self.stream_complete
            and self.required_stages_ok
            and self.response_nonempty
            and self.latency_ok
            and self.recall_ok
            and self.error is None
        )


DE_USERS = [
    "de_schueler_anna",
    "de_schueler_ben",
    "de_schueler_clara",
    "de_schueler_david",
    "de_schueler_ela",
]

EN_USERS = [
    "en_student_amy",
    "en_student_bob",
    "en_student_chloe",
    "en_student_dan",
    "en_student_eva",
]

DE_SUBJECTS = ["mathematik", "deutsch", "englisch", "sachkunde", "geschichte", "erdkunde", "kunst"]
EN_SUBJECTS = ["math", "english", "german", "science", "history", "geography", "art"]

DE_FACTS = [
    ("name", "Mia"),
    ("klasse", "3b"),
    ("lieblingsfach", "Mathematik"),
    ("haustier", "Hamster"),
    ("lieblingsfarbe", "Gruen"),
    ("lieblingsobst", "Apfel"),
    ("lieblingsbuch", "Der kleine Drache"),
    ("hobby", "Malen"),
    ("schule", "Sonnenweg Grundschule"),
    ("stadt", "Koeln"),
]

EN_FACTS = [
    ("name", "Liam"),
    ("class", "4A"),
    ("favorite subject", "Math"),
    ("pet", "Rabbit"),
    ("favorite color", "Blue"),
    ("favorite fruit", "Banana"),
    ("favorite book", "Treasure Island"),
    ("hobby", "Drawing"),
    ("school", "Riverbank Primary"),
    ("city", "London"),
]


def _difficulty_for_grade(grade: int) -> str:
    if grade <= 2:
        return "easy"
    if grade <= 4:
        return "medium"
    return "hard"


def _de_subject_question(subject: str, grade: int, variant: int) -> str:
    bank = {
        "mathematik": {
            1: ["Rechne: 8 + 7.", "Wie viel ist 14 - 6?"],
            2: ["Rechne: 6 * 4.", "Wie viel ist 36 : 6?"],
            3: ["Was ist ein Bruch? Erklaere mit einem Pizza-Beispiel.", "Rechne: 3/4 + 1/4."],
            4: ["Rechne mit Kommazahlen: 3,5 + 2,4.", "Was bedeutet Flaecheninhalt?"],
            5: ["Loese: 3x + 5 = 20.", "Erklaere den Unterschied zwischen Umfang und Flaeche."],
        },
        "deutsch": {
            1: ["Bilde einen Satz mit dem Wort 'Haus'.", "Was ist ein Nomen?"],
            2: ["Nenne drei Woerter mit 'sch'.", "Was ist der Unterschied zwischen Punkt und Fragezeichen?"],
            3: ["Was ist ein Verb? Gib zwei Beispiele.", "Bilde einen Satz im Praeteritum."],
            4: ["Erklaere den Unterschied zwischen woertlicher Rede und Erzaehlung.", "Was ist ein Adjektiv und wozu braucht man es?"],
            5: ["Was ist ein Hauptsatz und was ist ein Nebensatz?", "Erklaere kurz, was ein Argument in einem Aufsatz ist."],
        },
        "englisch": {
            1: ["Wie sagt man 'Katze' auf Englisch?", "Nenne drei Farben auf Englisch."],
            2: ["Wie sagt man 'Ich bin 8 Jahre alt' auf Englisch?", "Bilde einen einfachen englischen Satz mit 'I like'."],
            3: ["Erklaere den Unterschied zwischen 'is' und 'are'.", "Uebersetze: 'Wir spielen im Park.'"],
            4: ["Wann benutzt man 'do' und wann 'does'?", "Bilde eine Frage im Simple Present."],
            5: ["Erklaere kurz den Unterschied zwischen Simple Past und Present Perfect.", "Schreibe zwei englische Saetze ueber deine Schule."],
        },
        "sachkunde": {
            1: ["Warum brauchen Pflanzen Wasser?", "Was brauchen Menschen zum Atmen?"],
            2: ["Erklaere den Wasserkreislauf ganz einfach.", "Warum gibt es Tag und Nacht?"],
            3: ["Was ist der Unterschied zwischen Wirbeltieren und Insekten?", "Warum ist Muelltrennung wichtig?"],
            4: ["Erklaere kurz, wie ein einfacher Stromkreis funktioniert.", "Was ist erneuerbare Energie?"],
            5: ["Was ist der Unterschied zwischen Wetter und Klima?", "Erklaere Nahrungsketten mit einem Beispiel."],
        },
        "geschichte": {
            1: ["Was ist ein Museum?", "Warum lernen wir etwas ueber frueher?"],
            2: ["Wer waren Ritter?", "Was war eine Burg?"],
            3: ["Nenne einen Unterschied zwischen frueherer und heutiger Schule.", "Was ist eine Quelle in der Geschichte?"],
            4: ["Wer war Johannes Gutenberg?", "Warum war der Buchdruck wichtig?"],
            5: ["Was war die industrielle Revolution in einfachen Worten?", "Warum veraendern Erfindungen die Gesellschaft?"],
        },
        "erdkunde": {
            1: ["Nenne einen Kontinent.", "Was ist ein Fluss?"],
            2: ["Was ist der Unterschied zwischen Dorf und Stadt?", "Warum haben Laender Grenzen?"],
            3: ["Erklaere den Unterschied zwischen Karte und Globus.", "Was ist ein Gebirge?"],
            4: ["Warum gibt es verschiedene Klimazonen?", "Was bedeutet Bevoelkerungsdichte?"],
            5: ["Erklaere kurz, warum Vulkane entstehen.", "Was ist der Unterschied zwischen Import und Export?"],
        },
        "kunst": {
            1: ["Welche Farben ergeben zusammen Orange?", "Was ist dein Lieblingsmaterial zum Basteln?"],
            2: ["Was ist ein Selbstportraet?", "Wie kann man Tiefe in einer Zeichnung zeigen?"],
            3: ["Was sind warme und kalte Farben?", "Wie wirkt ein Bild mit starken Kontrasten?"],
            4: ["Was bedeutet Perspektive beim Zeichnen?", "Nenne ein beruehmtes Gemalde und warum es bekannt ist."],
            5: ["Erklaere kurz den Unterschied zwischen Realismus und Abstraktion.", "Wie kann Kunst eine Meinung ausdruecken?"],
        },
    }
    questions = bank[subject][grade]
    return questions[variant % len(questions)]


def _en_subject_question(subject: str, grade: int, variant: int) -> str:
    bank = {
        "math": {
            1: ["Solve: 9 + 6.", "What is 15 - 8?"],
            2: ["Solve: 7 * 5.", "What is 42 / 6?"],
            3: ["What is a fraction? Give a pizza example.", "Solve: 2/3 + 1/3."],
            4: ["Add decimals: 4.6 + 1.3.", "What does area mean?"],
            5: ["Solve: 4x + 8 = 28.", "Explain the difference between perimeter and area."],
        },
        "english": {
            1: ["Make one sentence with the word 'tree'.", "What is a noun?"],
            2: ["Write three words that rhyme with 'cat'.", "When do we use a question mark?"],
            3: ["What is a verb? Give two examples.", "Write one sentence in past tense."],
            4: ["Explain direct speech vs reported speech.", "What is an adjective and why do we use it?"],
            5: ["What is a main clause and a subordinate clause?", "What is an argument in a short essay?"],
        },
        "german": {
            1: ["How do you say 'dog' in German?", "Name two colors in German."],
            2: ["How do you say 'I am 9 years old' in German?", "Write a simple German sentence with 'ich mag'."],
            3: ["Explain when to use 'der' and 'die' in simple terms.", "Translate: 'We play in the garden.' into German."],
            4: ["What is the difference between 'du' and 'Sie'?", "Write one question in German."],
            5: ["Explain dative vs accusative in one simple example.", "Write two German sentences about school."],
        },
        "science": {
            1: ["Why do plants need water?", "What do people need to breathe?"],
            2: ["Explain the water cycle in simple words.", "Why do we have day and night?"],
            3: ["What is the difference between mammals and insects?", "Why is recycling important?"],
            4: ["How does a simple electric circuit work?", "What is renewable energy?"],
            5: ["What is the difference between weather and climate?", "Explain a food chain with one example."],
        },
        "history": {
            1: ["What is a museum?", "Why do we learn about the past?"],
            2: ["Who were knights?", "What was a castle used for?"],
            3: ["Name one difference between schools in the past and today.", "What is a historical source?"],
            4: ["Who was Johannes Gutenberg?", "Why was the printing press important?"],
            5: ["What was the industrial revolution in simple words?", "How can inventions change society?"],
        },
        "geography": {
            1: ["Name one continent.", "What is a river?"],
            2: ["What is the difference between a village and a city?", "Why do countries have borders?"],
            3: ["What is the difference between a map and a globe?", "What is a mountain range?"],
            4: ["Why do we have different climate zones?", "What does population density mean?"],
            5: ["Why do volcanoes happen?", "What is the difference between import and export?"],
        },
        "art": {
            1: ["Which colors make orange?", "What is your favorite art material?"],
            2: ["What is a self-portrait?", "How can you show depth in a drawing?"],
            3: ["What are warm and cool colors?", "How does contrast change a picture?"],
            4: ["What is perspective in drawing?", "Name a famous painting and why it is known."],
            5: ["Explain realism vs abstraction in simple words.", "How can art express an opinion?"],
        },
    }
    questions = bank[subject][grade]
    return questions[variant % len(questions)]


def _store_prompt(lang: str, key: str, value: str) -> str:
    if lang == "de":
        return (
            f"Bitte speichere diesen Fakt fuer spaeter: "
            f"SCHLUESSEL={key} | WERT={value}. "
            f"Bestaetige kurz mit 'gespeichert'."
        )
    return (
        f"Please save this fact for later: "
        f"KEY={key} | VALUE={value}. "
        f"Confirm briefly with 'saved'."
    )


def _recall_prompt(lang: str, key: str) -> str:
    if lang == "de":
        return (
            f"Ich habe dir einen Fakt mit SCHLUESSEL={key} gespeichert. "
            f"Was war der WERT? Antworte nur mit dem gespeicherten Wert, nichts weiter."
        )
    return (
        f"I stored a fact with KEY={key}. "
        f"What was the VALUE? Answer only with the stored value, nothing else."
    )


def _build_user_turns(user_id: str, language: str, turns_per_user: int = 100) -> list[TurnSpec]:
    turns: list[TurnSpec] = []
    subjects = DE_SUBJECTS if language == "de" else EN_SUBJECTS
    facts = DE_FACTS if language == "de" else EN_FACTS

    stored_count = 0
    for i in range(turns_per_user):
        burst = (i // 10) + 1
        grade = GRADES[i % len(GRADES)]
        difficulty = _difficulty_for_grade(grade)

        phase = i % 10
        if phase in (0, 6):
            fact_idx = stored_count % len(facts)
            key, value = facts[fact_idx]
            stored_count += 1
            turns.append(
                TurnSpec(
                    user_id=user_id,
                    language=language,
                    grade=grade,
                    subject="memory",
                    message=_store_prompt(language, key, value),
                    topic="memory_store",
                    difficulty="easy",
                    burst=burst,
                    expect_keywords=[],
                )
            )
            continue

        if phase in (3, 8) and stored_count > 0:
            recall_idx = (i + stored_count) % stored_count
            key, value = facts[recall_idx % len(facts)]
            expect = [value.lower()]
            if " " in value:
                expect.extend([p.lower() for p in value.split() if p])
            turns.append(
                TurnSpec(
                    user_id=user_id,
                    language=language,
                    grade=grade,
                    subject="memory",
                    message=_recall_prompt(language, key),
                    topic="memory_recall",
                    difficulty="easy",
                    burst=burst,
                    expect_keywords=expect,
                )
            )
            continue

        subject = subjects[(i + burst) % len(subjects)]
        if language == "de":
            msg = _de_subject_question(subject, grade, i)
        else:
            msg = _en_subject_question(subject, grade, i)

        turns.append(
            TurnSpec(
                user_id=user_id,
                language=language,
                grade=grade,
                subject=subject,
                message=msg,
                topic=f"{subject}_grade_{grade}",
                difficulty=difficulty,
                burst=burst,
            )
        )

    return turns


def build_corpus() -> list[TurnSpec]:
    corpus: list[TurnSpec] = []

    for uid in DE_USERS:
        corpus.extend(_build_user_turns(uid, "de", turns_per_user=100))
    for uid in EN_USERS:
        corpus.extend(_build_user_turns(uid, "en", turns_per_user=100))

    return corpus


def _configure_encoding() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _read_sse(body: bytes) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    current_event = ""
    for raw in body.splitlines():
        line = raw.decode("utf-8", errors="replace").strip()
        if not line:
            continue
        if line.startswith("event:"):
            current_event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            events.append((current_event, line.split(":", 1)[1].strip()))
    return events


def _call_stream(session_id: str, user_id: str, message: str) -> dict:
    payload = json.dumps(
        {
            "session_id": session_id,
            "user_id": user_id,
            "message": message,
            "max_tokens": MAX_TOKENS,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{BASE_URL}/chat/stream",
        data=payload,
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        body = resp.read()

    events = _read_sse(body)

    progress_stages: list[str] = []
    memory_effect = False
    chunks: list[str] = []
    final_payload: dict = {}
    has_final = False
    has_done = False
    tools_used: list[str] = []

    for evt, data in events:
        try:
            obj = json.loads(data)
        except Exception:
            obj = {}

        if evt == "progress":
            stage = str(obj.get("stage") or "")
            if stage:
                progress_stages.append(stage)
            if stage == "memory_effect_detected":
                memory_effect = True
        elif evt == "chunk":
            text = obj.get("text", "")
            if isinstance(text, str) and text:
                chunks.append(text)
        elif evt == "final":
            has_final = True
            final_payload = obj if isinstance(obj, dict) else {}
            tools_used = final_payload.get("tools_used") or []
        elif evt == "done":
            has_done = True

    response_text = "".join(chunks).strip()
    if not response_text:
        response_text = str(final_payload.get("response") or "").strip()

    return {
        "progress_stages": progress_stages,
        "memory_effect_detected": memory_effect,
        "response_text": response_text,
        "has_final": has_final,
        "has_done": has_done,
        "has_chunks": bool(chunks),
        "tools_used": tools_used if isinstance(tools_used, list) else [],
    }


def _audit_turn(
    turn_index: int,
    spec: TurnSpec,
    session_id: str,
    result: dict,
    elapsed_s: float,
    error: Optional[str],
) -> TurnResult:
    if error:
        return TurnResult(
            turn_index=turn_index,
            user_id=spec.user_id,
            language=spec.language,
            grade=spec.grade,
            subject=spec.subject,
            session_id=session_id,
            message=spec.message,
            topic=spec.topic,
            difficulty=spec.difficulty,
            burst=spec.burst,
            elapsed_s=elapsed_s,
            response_excerpt="",
            progress_stages=[],
            memory_effect_detected=False,
            tools_used=[],
            stream_complete=False,
            required_stages_ok=False,
            response_nonempty=False,
            latency_ok=False,
            recall_ok=False,
            has_recall_check=bool(spec.expect_keywords),
            error=error,
        )

    stages = result["progress_stages"]
    response = result["response_text"]
    budget = LATENCY_BUDGET.get(spec.difficulty, 60.0)

    recall_ok = True
    if spec.expect_keywords:
        resp_lower = response.lower()
        recall_ok = any(kw in resp_lower for kw in spec.expect_keywords)

    min_response_len = 1 if spec.expect_keywords else 10

    return TurnResult(
        turn_index=turn_index,
        user_id=spec.user_id,
        language=spec.language,
        grade=spec.grade,
        subject=spec.subject,
        session_id=session_id,
        message=spec.message,
        topic=spec.topic,
        difficulty=spec.difficulty,
        burst=spec.burst,
        elapsed_s=round(elapsed_s, 3),
        response_excerpt=response[:200],
        progress_stages=stages,
        memory_effect_detected=result["memory_effect_detected"],
        tools_used=result["tools_used"],
        stream_complete=result["has_chunks"] and result["has_final"] and result["has_done"],
        required_stages_ok=("accepted" in stages and "orchestration_complete" in stages),
        response_nonempty=len(response.strip()) >= min_response_len,
        latency_ok=elapsed_s <= budget,
        recall_ok=recall_ok,
        has_recall_check=bool(spec.expect_keywords),
        error=None,
    )


def _user_session_id(user_id: str) -> str:
    return f"bench1000-{user_id}-{uuid.uuid4().hex[:8]}"


def run_benchmark(corpus: list[TurnSpec]) -> list[TurnResult]:
    results: list[TurnResult] = []

    user_order: list[str] = []
    by_user: dict[str, list[TurnSpec]] = {}
    for spec in corpus:
        if spec.user_id not in by_user:
            by_user[spec.user_id] = []
            user_order.append(spec.user_id)
        by_user[spec.user_id].append(spec)

    global_turn = 0
    for user_id in user_order:
        if USER_FILTER and not any(user_id.startswith(f) for f in USER_FILTER):
            print(f"[SKIP] {user_id}")
            continue

        session_id = _user_session_id(user_id)
        user_turns = by_user[user_id]
        print(f"\n{'='*60}")
        print(f"USER: {user_id}  session={session_id}  turns={len(user_turns)}")
        print(f"{'='*60}")

        for spec in user_turns:
            global_turn += 1
            tag = f"[{global_turn:04d}/{len(corpus)}] {spec.user_id} g{spec.grade} {spec.subject} ({spec.difficulty})"
            print(f"  {tag}")

            t0 = time.monotonic()
            error: Optional[str] = None
            call_result: dict = {}

            if DRY_RUN:
                dry_response = "[DRY RUN - no HTTP]"
                if spec.expect_keywords:
                    dry_response = spec.expect_keywords[0]
                call_result = {
                    "progress_stages": ["accepted", "orchestration_started", "orchestration_complete"],
                    "memory_effect_detected": spec.topic == "memory_recall",
                    "response_text": dry_response,
                    "has_final": True,
                    "has_done": True,
                    "has_chunks": True,
                    "tools_used": [],
                }
            else:
                try:
                    call_result = _call_stream(session_id, user_id, spec.message)
                except Exception as exc:
                    error = str(exc)

            elapsed = time.monotonic() - t0
            tr = _audit_turn(global_turn, spec, session_id, call_result, elapsed, error)
            results.append(tr)

            status = "PASS" if tr.passed else "FAIL"
            recall_flag = ""
            if spec.expect_keywords:
                recall_flag = " recall=OK" if tr.recall_ok else " recall=MISS"
            print(f"     [{status}] {elapsed:.1f}s{recall_flag}" + (f" ERR={error}" if error else ""))

    return results


def _build_summary(results: list[TurnResult], started_at: str) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    latencies = [r.elapsed_s for r in results if r.error is None]
    latencies_sorted = sorted(latencies)
    p95 = latencies_sorted[int(0.95 * len(latencies_sorted)) - 1] if latencies_sorted else 0

    by_difficulty: dict[str, dict] = {}
    for d in ("easy", "medium", "hard"):
        subset = [r for r in results if r.difficulty == d]
        p = sum(1 for r in subset if r.passed)
        by_difficulty[d] = {
            "total": len(subset),
            "passed": p,
            "failed": len(subset) - p,
            "pass_rate_pct": round((p / len(subset) * 100), 1) if subset else 0,
        }

    by_language: dict[str, dict] = {}
    for lang in ("de", "en"):
        subset = [r for r in results if r.language == lang]
        p = sum(1 for r in subset if r.passed)
        by_language[lang] = {
            "total": len(subset),
            "passed": p,
            "failed": len(subset) - p,
            "pass_rate_pct": round((p / len(subset) * 100), 1) if subset else 0,
        }

    by_grade: dict[str, dict] = {}
    for g in GRADES:
        subset = [r for r in results if r.grade == g]
        p = sum(1 for r in subset if r.passed)
        by_grade[str(g)] = {
            "total": len(subset),
            "passed": p,
            "failed": len(subset) - p,
            "pass_rate_pct": round((p / len(subset) * 100), 1) if subset else 0,
        }

    by_subject: dict[str, dict] = {}
    for r in results:
        if r.subject not in by_subject:
            by_subject[r.subject] = {"total": 0, "passed": 0}
        by_subject[r.subject]["total"] += 1
        if r.passed:
            by_subject[r.subject]["passed"] += 1

    recall_turns = [r for r in results if r.topic == "memory_recall"]
    recall_ok = sum(1 for r in recall_turns if r.recall_ok)

    return {
        "started_at": started_at,
        "base_url": BASE_URL,
        "dry_run": DRY_RUN,
        "total_turns": total,
        "passed": passed,
        "failed": failed,
        "pass_rate_pct": round((passed / total * 100), 1) if total else 0,
        "avg_latency_s": round(sum(latencies) / len(latencies), 2) if latencies else 0,
        "p95_latency_s": round(p95, 2),
        "max_latency_s": round(max(latencies), 2) if latencies else 0,
        "memory_effect_detected_count": sum(1 for r in results if r.memory_effect_detected),
        "recall_turns": {
            "total": len(recall_turns),
            "recall_ok": recall_ok,
            "recall_rate_pct": round((recall_ok / len(recall_turns) * 100), 1) if recall_turns else 0,
        },
        "by_difficulty": by_difficulty,
        "by_language": by_language,
        "by_grade": by_grade,
        "by_subject": by_subject,
    }


def _write_reports(results: list[TurnResult], summary: dict) -> tuple[str, str]:
    os.makedirs(AUDIT_DIR, exist_ok=True)
    jsonl_path = os.path.join(AUDIT_DIR, f"benchmark_1000q_primary_{_TS}.jsonl")
    summary_path = os.path.join(AUDIT_DIR, f"benchmark_1000q_primary_{_TS}_summary.json")

    with open(jsonl_path, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    return jsonl_path, summary_path


def main() -> int:
    _configure_encoding()
    started_at = datetime.now(timezone.utc).isoformat()

    corpus = build_corpus()

    print("LIARA 1000-Fragen Primary-School Learning Benchmark")
    print(f"  API:     {BASE_URL}")
    print(f"  Turns:   {len(corpus)}")
    print(f"  Timeout: {TIMEOUT_S}s")
    print(f"  DryRun:  {DRY_RUN}")
    print(f"  Filter:  {USER_FILTER or 'all users'}")
    print()

    if DRY_RUN:
        by_user: dict[str, int] = {}
        for s in corpus:
            by_user[s.user_id] = by_user.get(s.user_id, 0) + 1
        print("[DRY RUN] Corpus users:")
        for uid, cnt in by_user.items():
            print(f"  {uid}: {cnt}")
        print(f"  Total: {len(corpus)}")
        print()

    results = run_benchmark(corpus)
    summary = _build_summary(results, started_at)

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"  Total turns:      {summary['total_turns']}")
    print(f"  Passed:           {summary['passed']} ({summary['pass_rate_pct']}%)")
    print(f"  Failed:           {summary['failed']}")
    print(f"  Latency avg/p95:  {summary['avg_latency_s']}s / {summary['p95_latency_s']}s")
    print(
        "  Recall:           "
        f"{summary['recall_turns']['recall_ok']}/{summary['recall_turns']['total']} "
        f"({summary['recall_turns']['recall_rate_pct']}%)"
    )
    print("  By language:")
    for lang in ("de", "en"):
        row = summary["by_language"][lang]
        print(f"    {lang}: {row['passed']}/{row['total']} ({row['pass_rate_pct']}%)")

    jsonl_path, summary_path = _write_reports(results, summary)
    print(f"\n  Audit-JSONL:  {jsonl_path}")
    print(f"  Summary-JSON: {summary_path}")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
