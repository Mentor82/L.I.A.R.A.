"""LIARA 200-Fragen Multi-User Benchmark & Audit.

Testet 8 Nutzerprofile mit je 15-30 Fragen pro Nutzer (Gesamt: 200 Turns).
Jeder Nutzer hat Burst-Struktur (1-8 Fragen/Thema), Themenwechsel und
Schwierigkeitsgrade von Smalltalk bis fachlicher Komplexitaet.

Auditiert pro Turn:
- Stream-Vollstaendigkeit (progress / chunks / final / done)
- Pflicht-Stages (accepted, orchestration_complete)
- Antwort nicht leer
- Latenz vs. Budget (easy<=45s, medium<=60s, hard<=90s)
- Recall-Checks (keywords in Antwort, wo erwartet)
- Memory-Effect-Signal (wo injiziert)

Ausgabe:
- Fortschritt auf stdout (kompakt)
- Audit-JSONL: logs/tests/benchmark_200q_<ts>.jsonl
- Summary-JSON:  logs/tests/benchmark_200q_<ts>_summary.json

Umgebungsvariablen:
  LIARA_API_BASE_URL           Ziel-API (default http://127.0.0.1:8010)
  BENCHMARK_MAX_TOKENS         Max-Tokens pro Antwort (default 512)
  BENCHMARK_TIMEOUT_S          Request-Timeout in Sekunden (default 120)
  BENCHMARK_AUDIT_DIR          Override Ausgabeverzeichnis
  BENCHMARK_USER_FILTER        Komma-getrennte user_id-Praefix-Filter
  BENCHMARK_DRY_RUN            1 = kein HTTP, nur Corpus-Dump
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
MAX_TOKENS = int(os.getenv("BENCHMARK_MAX_TOKENS", "512"))
TIMEOUT_S = int(os.getenv("BENCHMARK_TIMEOUT_S", "120"))
USER_FILTER = [u.strip() for u in os.getenv("BENCHMARK_USER_FILTER", "").split(",") if u.strip()]
DRY_RUN = os.getenv("BENCHMARK_DRY_RUN", "0") == "1"

_TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
_DEFAULT_AUDIT_DIR = os.path.join(os.path.dirname(__file__), "..", "logs", "tests")
AUDIT_DIR = os.getenv("BENCHMARK_AUDIT_DIR", _DEFAULT_AUDIT_DIR)

LATENCY_BUDGET = {"easy": 45.0, "medium": 60.0, "hard": 90.0}

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class TurnSpec:
    user_id: str
    message: str
    topic: str
    difficulty: str        # easy | medium | hard
    burst: int             # burst index within user
    expect_keywords: List[str] = field(default_factory=list)  # recall check
    plant_fact: Optional[str] = None   # tag, used to track what was stored


@dataclass
class TurnResult:
    turn_index: int
    user_id: str
    session_id: str
    message: str
    topic: str
    difficulty: str
    burst: int
    elapsed_s: float
    response_excerpt: str
    progress_stages: List[str]
    memory_effect_detected: bool
    tools_used: List[str]
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


# ---------------------------------------------------------------------------
# Corpus — 200 turns across 8 user personas
# ---------------------------------------------------------------------------

def _build_corpus() -> List[TurnSpec]:
    turns: List[TurnSpec] = []

    def add(user_id, burst, topic, difficulty, message,
            expect_keywords=None, plant_fact=None):
        turns.append(TurnSpec(
            user_id=user_id,
            message=message,
            topic=topic,
            difficulty=difficulty,
            burst=burst,
            expect_keywords=expect_keywords or [],
            plant_fact=plant_fact,
        ))

    # -----------------------------------------------------------------------
    # User 1: anna_techie  (30 Fragen)
    # Python-Entwicklerin, wechselt zu Reise und Smalltalk
    # -----------------------------------------------------------------------
    u = "anna_techie"

    # Burst 1 – Smalltalk (2)
    add(u, 1, "smalltalk", "easy", "Hallo! Wie laeuft es heute bei dir?")
    add(u, 1, "smalltalk", "easy", "Was kannst du mir so alles helfen?",
        plant_fact="anna_intro_done")

    # Burst 2 – Python Basics (3)
    add(u, 2, "python_basics", "easy",
        "Was ist der Unterschied zwischen einer Liste und einem Tuple in Python?")
    add(u, 2, "python_basics", "easy",
        "Wie erstelle ich ein Dictionary in Python mit drei Schluesseln?")
    add(u, 2, "python_basics", "easy",
        "Erklaere mir List Comprehensions in Python mit einem kurzen Beispiel.")

    # Burst 3 – Python Intermediate (4)
    add(u, 3, "python_intermediate", "medium",
        "Was ist der Unterschied zwischen *args und **kwargs in Python?")
    add(u, 3, "python_intermediate", "medium",
        "Erklaere das Konzept der Decorators in Python und zeige ein einfaches Beispiel.")
    add(u, 3, "python_intermediate", "medium",
        "Wie funktioniert Context Manager in Python (with-Statement) intern?")
    add(u, 3, "python_intermediate", "medium",
        "Was ist der Unterschied zwischen `is` und `==` in Python?")

    # Burst 4 – Themenwechsel Smalltalk (1)
    add(u, 4, "smalltalk", "easy",
        "Uebrigens, ich trinke immer Gruentee beim Programmieren.",
        plant_fact="anna_fact_tee")

    # Burst 5 – Datenbanken SQL (3)
    add(u, 5, "databases_sql", "medium",
        "Erklaere den Unterschied zwischen INNER JOIN und LEFT JOIN in SQL.")
    add(u, 5, "databases_sql", "medium",
        "Was ist ein Index in einer relationalen Datenbank und wann sollte man einen anlegen?")
    add(u, 5, "databases_sql", "medium",
        "Wie funktioniert eine GROUP BY-Klausel in SQL? Zeige ein Beispiel.")

    # Burst 6 – Python Advanced (5)
    add(u, 6, "python_advanced", "hard",
        "Erklaere GIL (Global Interpreter Lock) in Python und seine Auswirkungen auf Threading.")
    add(u, 6, "python_advanced", "hard",
        "Wie implementiere ich einen asynchronen HTTP-Client in Python mit asyncio und aiohttp?")
    add(u, 6, "python_advanced", "hard",
        "Was ist der Unterschied zwischen Generators und Coroutines in Python?")
    add(u, 6, "python_advanced", "hard",
        "Erklaere das Metaclass-Konzept in Python mit einem praktischen Anwendungsfall.")
    add(u, 6, "python_advanced", "hard",
        "Wie implementiere ich ein eigenes Iterator-Protokoll in Python?")

    # Burst 7 – Themenwechsel Reise (2)
    add(u, 7, "travel", "easy",
        "Welche drei europaeischen Staedte wuerden sich fuer einen kurzen Wochenend-Trip eignen?")
    add(u, 7, "travel", "easy",
        "Was sollte ich unbedingt in Lissabon gesehen haben?")

    # Burst 8 – Memory Recall (2)
    add(u, 8, "memory_recall", "easy",
        "Was trinke ich immer beim Programmieren?",
        expect_keywords=["tee", "gruentee", "gruen"])
    add(u, 8, "memory_recall", "easy",
        "Womit haben wir unser Gespraech heute begonnen – welches Thema war zuerst?",
        expect_keywords=["hallo", "smalltalk", "python", "hilf", "gespraech"])

    # Burst 9 – Async Programming (4)
    add(u, 9, "async_programming", "hard",
        "Was ist der Unterschied zwischen asyncio.gather und asyncio.wait?")
    add(u, 9, "async_programming", "hard",
        "Wie verhindere ich Race Conditions bei parallelen asyncio-Tasks in Python?")
    add(u, 9, "async_programming", "hard",
        "Erklaere das Backpressure-Konzept in async Streaming-Systemen.")
    add(u, 9, "async_programming", "hard",
        "Wann sollte ich ThreadPoolExecutor vs ProcessPoolExecutor in asyncio verwenden?")

    # Burst 10 – Abschluss (2)
    add(u, 10, "closing", "easy", "Das war sehr hilfreich, danke!")
    add(u, 10, "closing", "easy", "Auf Wiedersehen!")

    # anna_techie Gesamt: 2+3+4+1+3+5+2+2+4+2 = 28 → +2 = 30
    add(u, 5, "databases_sql", "hard",
        "Erklaere den Unterschied zwischen normalisierten und denormalisierten Datenbankstrukturen und wann man welche bevorzugt.")
    add(u, 6, "python_advanced", "hard",
        "Wie debuggt man Memory Leaks in einem langlebigen Python-Prozess? Welche Tools und Methoden gibt es?")

    # -----------------------------------------------------------------------
    # User 2: bob_analyst  (25 Fragen)
    # Datenanalyst, Mathe + Statistik, Themenwechsel Kochen
    # -----------------------------------------------------------------------
    u = "bob_analyst"

    # Burst 1 – Mathe Easy (3)
    add(u, 1, "math_easy", "easy", "Was ist die Ableitung von f(x) = 3x^2 + 2x + 1?")
    add(u, 1, "math_easy", "easy",
        "Berechne: Wenn 15 Prozent von X gleich 90 sind, was ist X?",
        expect_keywords=["600"])
    add(u, 1, "math_easy", "easy",
        "Was ist der groesste gemeinsame Teiler (GGT) von 48 und 36?",
        expect_keywords=["12"])

    # Burst 2 – Statistik Medium (4)
    add(u, 2, "statistics", "medium",
        "Erklaere den Unterschied zwischen Mittelwert, Median und Modus.")
    add(u, 2, "statistics", "medium",
        "Was ist die Standardabweichung und wie interpretiert man sie?")
    add(u, 2, "statistics", "medium",
        "Erklaere den zentralen Grenzwertsatz in einfachen Worten.")
    add(u, 2, "statistics", "medium",
        "Was ist eine Konfidenzintervall und wie berechnet man es?")

    # Burst 3 – Themenwechsel Smalltalk (2)
    add(u, 3, "smalltalk", "easy",
        "Ich heisse uebrigens Bob und arbeite seit 5 Jahren als Datenanalyst.",
        plant_fact="bob_name_job")
    add(u, 3, "smalltalk", "easy", "Wie findest du eigentlich das Wetter heute?")

    # Burst 4 – Datenanalyse Medium (4)
    add(u, 4, "data_analysis", "medium",
        "Was ist der Unterschied zwischen Korrelation und Kausalitaet?")
    add(u, 4, "data_analysis", "medium",
        "Erklaere die Unterschiede zwischen supervised und unsupervised Learning.")
    add(u, 4, "data_analysis", "medium",
        "Was sind Ausreisser (Outliers) in einem Datensatz und wie geht man damit um?")
    add(u, 4, "data_analysis", "medium",
        "Was ist Feature Engineering und warum ist es wichtig beim Machine Learning?")

    # Burst 5 – Mathe Hard (4)
    add(u, 5, "math_hard", "hard",
        "Erklaere das Bayes-Theorem und nenne ein praktisches Anwendungsbeispiel.")
    add(u, 5, "math_hard", "hard",
        "Was ist eine Fourier-Transformation und wozu wird sie in der Praxis eingesetzt?")
    add(u, 5, "math_hard", "hard",
        "Erklaere den Unterschied zwischen parametrischen und nicht-parametrischen Tests in der Statistik.")
    add(u, 5, "math_hard", "hard",
        "Was ist der Unterschied zwischen Maximum Likelihood Estimation und Bayesianischer Inferenz?")

    # Burst 6 – Themenwechsel Kochen (1)
    add(u, 6, "cooking", "easy",
        "Hast du ein schnelles Rezept fuer Carbonara? Kurz und klar bitte.")

    # Burst 7 – Wahrscheinlichkeit Hard (5)
    add(u, 7, "probability", "hard",
        "Was ist der Monty-Hall-Effekt und warum ist die korrekte Antwort kontraintuitiv?")
    add(u, 7, "probability", "hard",
        "Erklaere den Unterschied zwischen bedingter und unbedingter Wahrscheinlichkeit.")
    add(u, 7, "probability", "hard",
        "Was ist ein Poisson-Prozess und in welchen Szenarien findet er Anwendung?")
    add(u, 7, "probability", "hard",
        "Was ist der Unterschied zwischen Binomialverteilung und Normalverteilung?")
    add(u, 7, "probability", "hard",
        "Erklaere das Law of Large Numbers und seinen Unterschied zum zentralen Grenzwertsatz.")

    # Burst 8 – Memory Recall (2)
    add(u, 8, "memory_recall", "medium",
        "Wie lange arbeite ich schon als Datenanalyst?",
        expect_keywords=["5", "fuenf", "jahren", "analyst"])
    add(u, 8, "memory_recall", "medium",
        "Was war das erste mathematische Thema, das wir besprochen haben?",
        expect_keywords=["abl", "ableitung", "mathe", "math", "f(x)"])

    # -----------------------------------------------------------------------
    # User 3: carol_casual  (15 Fragen)
    # Casual-User, Smalltalk-dominant, ein Memory-Plant
    # -----------------------------------------------------------------------
    u = "carol_casual"

    # Burst 1 – Greeting (1)
    add(u, 1, "greeting", "easy", "Hey, kannst du mir helfen?")

    # Burst 2 – Smalltalk (3)
    add(u, 2, "smalltalk", "easy", "Was machst du eigentlich so, wenn du nicht arbeitest?")
    add(u, 2, "smalltalk", "easy",
        "Ich mag es, im Sommer draussen zu lesen. Hast du Buchempfehlungen?")
    add(u, 2, "smalltalk", "easy", "Was denkst du, was macht Menschen gluecklich?")

    # Burst 3 – Hobby (2)
    add(u, 3, "hobbies", "easy",
        "Mein Lieblingsfilm ist uebrigens 'Inception'. Was weisst du darueber?",
        plant_fact="carol_lieblingsfilm")
    add(u, 3, "hobbies", "easy",
        "Ich koche gerne, besonders asiatische Kueche. Was sind einfache Thai-Gerichte?")

    # Burst 4 – Memory Plant (1)
    add(u, 4, "memory_plant", "easy",
        "Merke dir bitte: Ich wohne in Freiburg und arbeite als Krankenpflegerin.",
        plant_fact="carol_city_job")

    # Burst 5 – Smalltalk Switch (3)
    add(u, 5, "smalltalk", "easy", "Was haeltst du von kuenstlicher Intelligenz generell?")
    add(u, 5, "smalltalk", "easy",
        "Glaubst du, dass Roboter eines Tages wirklich kreativ sein koennen?")
    add(u, 5, "smalltalk", "easy", "Welche Sprachen kann ich am schnellsten lernen?")

    # Burst 6 – Memory Recall (1)
    add(u, 6, "memory_recall", "easy",
        "In welcher Stadt wohne ich und was arbeite ich?",
        expect_keywords=["freiburg", "pfleger", "krankenpfleger"])

    # Burst 7 – Random (2)
    add(u, 7, "general", "easy",
        "Was ist eigentlich der Unterschied zwischen Wetter und Klima?")
    add(u, 7, "general", "easy",
        "Warum ist der Himmel blau? Erklaer mir das einfach.")

    # Burst 8 – Closing (2)
    add(u, 8, "closing", "easy", "Super, danke fuer das nette Gespraech!")
    add(u, 8, "closing", "easy",
        "Welcher Film war nochmal mein Lieblingsfilm?",
        expect_keywords=["inception"])

    # -----------------------------------------------------------------------
    # User 4: david_power  (30 Fragen)
    # Power-User, sehr technisch, System-Design, Security
    # -----------------------------------------------------------------------
    u = "david_power"

    # Burst 1 – Complex Coding (5)
    add(u, 1, "complex_coding", "hard",
        "Erklaere das Reactor-Pattern und vergleiche es mit dem Proactor-Pattern.")
    add(u, 1, "complex_coding", "hard",
        "Wie implementiert man ein effizientes LRU-Cache in Python ohne externe Libraries?")
    add(u, 1, "complex_coding", "hard",
        "Was ist der Unterschied zwischen optimistischem und pessimistischem Locking in Datenbanken?")
    add(u, 1, "complex_coding", "hard",
        "Erklaere das CQRS-Pattern (Command Query Responsibility Segregation) mit Vor- und Nachteilen.")
    add(u, 1, "complex_coding", "hard",
        "Wie funktioniert Write-Ahead Logging (WAL) in PostgreSQL intern?")

    # Burst 2 – System Design (4)
    add(u, 2, "system_design", "hard",
        "Wie designst du ein hochverfuegbares Message-Queue-System fuer 1 Million Nachrichten/Sekunde?")
    add(u, 2, "system_design", "hard",
        "Erklaere den Unterschied zwischen Push- und Pull-basierten Streaming-Architekturen.")
    add(u, 2, "system_design", "hard",
        "Was sind die Vor- und Nachteile von Event Sourcing vs. klassischem CRUD?")
    add(u, 2, "system_design", "hard",
        "Wie implementiert man ein Circuit Breaker Pattern in einem Microservices-System?")

    # Burst 3 – Smalltalk Switch (1)
    add(u, 3, "smalltalk", "easy",
        "Kurze Pause – ich heisse David und entwickle Backend-Systeme seit 10 Jahren.",
        plant_fact="david_name_job")

    # Burst 4 – DB Optimization (4)
    add(u, 4, "database_optimization", "hard",
        "Erklaere Query-Plan-Analyse in PostgreSQL mit EXPLAIN ANALYZE.")
    add(u, 4, "database_optimization", "hard",
        "Wann und warum sollte man partielle Indizes statt vollstaendiger Indizes verwenden?")
    add(u, 4, "database_optimization", "hard",
        "Was ist das N+1 Problem in ORM-Systemen und wie loest man es?")
    add(u, 4, "database_optimization", "hard",
        "Erklaere Datenbanksharding-Strategien: Hash-Sharding vs. Range-Sharding.")

    # Burst 5 – API Design (4)
    add(u, 5, "api_design", "hard",
        "Was ist der Unterschied zwischen REST, GraphQL und gRPC – wann verwendet man was?")
    add(u, 5, "api_design", "hard",
        "Wie implementiert man idempotente API-Endpunkte fuer kritische Operationen?")
    add(u, 5, "api_design", "hard",
        "Erklaere Rate Limiting Strategien: Token Bucket vs. Leaky Bucket vs. Fixed Window.")
    add(u, 5, "api_design", "hard",
        "Was ist HATEOAS und ist es in der Praxis sinnvoll?")

    # Burst 6 – Philosophie Switch (2)
    add(u, 6, "philosophy", "medium",
        "Macht es einen Unterschied, ob eine KI 'versteht' oder nur 'simuliert zu verstehen'?")
    add(u, 6, "philosophy", "medium",
        "Was ist das 'Chinese Room' Argument von Searle und wie ist deine Einschaetzung?")

    # Burst 7 – Security (4)
    add(u, 7, "security", "hard",
        "Erklaere den Unterschied zwischen Authentifizierung und Autorisierung mit Beispielen.")
    add(u, 7, "security", "hard",
        "Was sind die haeufigsten OWASP Top-10-Sicherheitsluecken und wie verhindert man sie?")
    add(u, 7, "security", "hard",
        "Erklaere wie JWT-Tokens funktionieren und welche Sicherheitsrisiken sie haben.")
    add(u, 7, "security", "hard",
        "Was ist ein SQL-Injection-Angriff und wie verhindert man ihn vollstaendig?")

    # Burst 8 – Architecture (4)
    add(u, 8, "architecture", "hard",
        "Erklaere den CAP-Theorem und seine praktischen Auswirkungen auf Systemdesign.")
    add(u, 8, "architecture", "hard",
        "Was ist der Unterschied zwischen Saga-Pattern und Two-Phase Commit fuer verteilte Transaktionen?")
    add(u, 8, "architecture", "hard",
        "Wie designt man einen hochverfuegbaren Caching-Layer mit Redis fuer ein globales System?")
    add(u, 8, "architecture", "hard",
        "Erklaere Service Mesh Konzepte: Was macht Istio und warum braucht man es?")

    # Burst 9 – Memory Recall (2)
    add(u, 9, "memory_recall", "medium",
        "Wie lange entwickle ich schon Backend-Systeme?",
        expect_keywords=["10", "zehn", "jahren"])
    add(u, 9, "memory_recall", "medium",
        "Was war das erste Design-Pattern, das wir heute besprochen haben?",
        expect_keywords=["reactor", "proactor", "pattern"])

    # -----------------------------------------------------------------------
    # User 5: emma_student  (25 Fragen)
    # Schueler/Studentin, Geschichte, Naturwissenschaften, Mathe, Geographie
    # -----------------------------------------------------------------------
    u = "emma_student"

    # Burst 1 – Greeting (1)
    add(u, 1, "greeting", "easy", "Hi, ich bin Studentin und brauche Hilfe bei Hausaufgaben.")

    # Burst 2 – Geschichte Easy (3)
    add(u, 2, "history_easy", "easy",
        "Wann fiel die Berliner Mauer und was waren die Folgen?",
        expect_keywords=["1989", "neunzehnhundert"])
    add(u, 2, "history_easy", "easy",
        "Wer war Otto von Bismarck und welche Rolle spielte er bei der Reichsgruendung?")
    add(u, 2, "history_easy", "easy",
        "Was waren die Hauptursachen des Ersten Weltkriegs?")

    # Burst 3 – Naturwissenschaften Medium (4)
    add(u, 3, "science_medium", "medium",
        "Erklaere den Unterschied zwischen Photosynthese und Zellatmung.")
    add(u, 3, "science_medium", "medium",
        "Was ist der Unterschied zwischen Atomen und Molekuelen?")
    add(u, 3, "science_medium", "medium",
        "Erklaere das Periodensystem: Wie ist es aufgebaut und was bedeuten die Perioden?")
    add(u, 3, "science_medium", "medium",
        "Was ist der Unterschied zwischen kinetischer und potentieller Energie?")

    # Burst 4 – Mathe Switch (3)
    add(u, 4, "math_medium", "medium",
        "Erklaere den Satz des Pythagoras mit einem Beispiel.")
    add(u, 4, "math_medium", "medium",
        "Was sind Primzahlen und wie findet man sie effizient (Sieb des Eratosthenes)?")
    add(u, 4, "math_medium", "medium",
        "Was ist die Ableitung und was bedeutet sie geometrisch?")

    # Burst 5 – Sprachen Easy (2)
    add(u, 5, "languages", "easy",
        "Mein Name ist Emma und ich lerne gerade Franzoesisch.",
        plant_fact="emma_name_language")
    add(u, 5, "languages", "easy",
        "Was sind die besten Methoden, um schnell eine neue Sprache zu lernen?")

    # Burst 6 – Naturwissenschaften Hard (4)
    add(u, 6, "science_hard", "hard",
        "Erklaere die Relativitaetstheorie: Was ist der Unterschied zwischen spezieller und allgemeiner?")
    add(u, 6, "science_hard", "hard",
        "Was ist Quantenverschraenkung und warum ist sie paradox?")
    add(u, 6, "science_hard", "hard",
        "Erklaere CRISPR-Cas9: Wie funktioniert Genbearbeitung und welche ethischen Fragen gibt es?")
    add(u, 6, "science_hard", "hard",
        "Was ist der Unterschied zwischen Fusion und Fission und warum ist Kernfusion so schwer zu realisieren?")

    # Burst 7 – Geographie Switch (2)
    add(u, 7, "geography", "easy",
        "Was sind die fuenf groessten Laender der Welt nach Flaeche?")
    add(u, 7, "geography", "easy",
        "Warum liegt Island auf einer Insel und ist trotzdem so aktiv vulkanisch?")

    # Burst 8 – Geschichte Hard (4)
    add(u, 8, "history_hard", "hard",
        "Erklaere die Ursachen und Folgen des Zweiten Weltkriegs in drei klaren Abschnitten.")
    add(u, 8, "history_hard", "hard",
        "Was war der Kalte Krieg und warum ist er ohne direkten Militaerkonflikt verlaufen?")
    add(u, 8, "history_hard", "hard",
        "Erklaere die Franzoesische Revolution: Ursachen, Verlauf, Folgen.")
    add(u, 8, "history_hard", "hard",
        "Was sind die wichtigsten Phasen der Industriellen Revolution und ihre gesellschaftlichen Folgen?")

    # Burst 9 – Memory Recall (2)
    add(u, 9, "memory_recall", "easy",
        "Welche Fremdsprache lerne ich gerade?",
        expect_keywords=["franzoesisch", "franz", "french"])
    add(u, 9, "memory_recall", "easy",
        "In welchem Jahr fiel die Berliner Mauer – erinnerst du dich?",
        expect_keywords=["1989"])

    # -----------------------------------------------------------------------
    # User 6: frank_manager  (25 Fragen)
    # Fuehrungskraft, Planung, Team, Strategie
    # -----------------------------------------------------------------------
    u = "frank_manager"

    # Burst 1 – Greeting Professional (2)
    add(u, 1, "greeting", "easy", "Guten Tag, ich bin Abteilungsleiter und brauche Unterstuetzung.")
    add(u, 1, "greeting", "easy",
        "Ich heisse Frank und leite ein Team von 12 Entwicklern.",
        plant_fact="frank_name_team")

    # Burst 2 – Projektplanung Medium (4)
    add(u, 2, "project_planning", "medium",
        "Was sind die wichtigsten Unterschiede zwischen Scrum und Kanban?")
    add(u, 2, "project_planning", "medium",
        "Wie erstelle ich einen realistischen Projektplan mit Meilensteinen?")
    add(u, 2, "project_planning", "medium",
        "Was ist der kritische Pfad in einem Projektplan und wie berechnet man ihn?")
    add(u, 2, "project_planning", "medium",
        "Welche Risikobewertungsmethoden gibt es in der Projektplanung?")

    # Burst 3 – Meeting Summary Medium (3)
    add(u, 3, "meeting_summary", "medium",
        "Ich habe ein Meeting mit 5 Entwicklern, 1 Designer und 1 PO. Wie strukturiere ich ein effektives Standup?")
    add(u, 3, "meeting_summary", "medium",
        "Wie erkenne ich fruehzeitig, ob ein Entwickler blockiert ist, ohne ihn zu demotivieren?")
    add(u, 3, "meeting_summary", "medium",
        "Was sind typische Fallen bei der Einfuehrung agiler Methoden in etablierten Teams?")

    # Burst 4 – Smalltalk Switch (1)
    add(u, 4, "smalltalk", "easy",
        "Ich brauche kurz Pause – wie laeuft es in anderen Teams gerade so, hoerst du oft von Problemen?")

    # Burst 5 – Strategische Planung Hard (5)
    add(u, 5, "strategic_planning", "hard",
        "Wie entwickelt man eine 3-Jahres-Technologiestrategie fuer ein mittelstaendisches Software-Unternehmen?")
    add(u, 5, "strategic_planning", "hard",
        "Was ist OKR (Objectives and Key Results) und wie implementiert man es erfolgreich in einem Entwicklungsteam?")
    add(u, 5, "strategic_planning", "hard",
        "Wie bewertet man Tech-Schulden (Technical Debt) und priorisiert deren Abbau?")
    add(u, 5, "strategic_planning", "hard",
        "Erklaere das Conway's Law und seine Auswirkungen auf Organisationsdesign.")
    add(u, 5, "strategic_planning", "hard",
        "Wie fuehrt man eine 'Make or Buy'-Entscheidung fuer eine Kerntechnologie durch?")

    # Burst 6 – Team Management Medium (4)
    add(u, 6, "team_management", "medium",
        "Wie gibt man konstruktives Feedback in einem 1:1-Gespraech?")
    add(u, 6, "team_management", "medium",
        "Was sind typische Zeichen von Burnout in einem Entwicklungsteam und wie reagiert man?")
    add(u, 6, "team_management", "medium",
        "Wie integriert man Remote-Entwickler gleichberechtigt in ein Hybrid-Team?")
    add(u, 6, "team_management", "medium",
        "Wie misst man die Produktivitaet eines Entwicklungsteams sinnvoll?")

    # Burst 7 – Risikoanalyse Hard (4)
    add(u, 7, "risk_analysis", "hard",
        "Erklaere die FMEA-Methode (Failure Mode and Effects Analysis) fuer Software-Projekte.")
    add(u, 7, "risk_analysis", "hard",
        "Wie erstellt man eine Business-Impact-Analyse fuer ein kritisches System?")
    add(u, 7, "risk_analysis", "hard",
        "Was ist ein Disaster-Recovery-Plan und was sind die Kernelemente?")
    add(u, 7, "risk_analysis", "hard",
        "Wie bewertet man das Risiko von Third-Party-Abhaengigkeiten in einer Software-Architektur?")

    # Burst 8 – Memory Recall (2)
    add(u, 8, "memory_recall", "medium",
        "Wie gross ist mein Team?",
        expect_keywords=["12", "zwoelf", "entwickler"])
    add(u, 8, "memory_recall", "medium",
        "Was war das erste agile Thema, das ich angesprochen habe?",
        expect_keywords=["scrum", "kanban", "agil"])

    # -----------------------------------------------------------------------
    # User 7: greta_scientist  (25 Fragen)
    # Wissenschaftlerin, Chemie, Biologie, Physik
    # -----------------------------------------------------------------------
    u = "greta_scientist"

    # Burst 1 – Chemie Basics (3)
    add(u, 1, "chemistry_basics", "easy",
        "Was ist eine kovalente Bindung und wie unterscheidet sie sich von einer ionischen Bindung?")
    add(u, 1, "chemistry_basics", "easy",
        "Erklaere den pH-Wert: Was bedeutet sauer, neutral und basisch?")
    add(u, 1, "chemistry_basics", "easy",
        "Was ist der Unterschied zwischen exothermer und endothermer Reaktion?")

    # Burst 2 – Biologie Medium (4)
    add(u, 2, "biology_medium", "medium",
        "Erklaere den Unterschied zwischen DNA und RNA.")
    add(u, 2, "biology_medium", "medium",
        "Was ist mitose und wie unterscheidet sie sich von meiose?")
    add(u, 2, "biology_medium", "medium",
        "Erklaere das Prinzip der natueerlichen Selektion nach Darwin.")
    add(u, 2, "biology_medium", "medium",
        "Was ist der Unterschied zwischen prokaryotischen und eukaryotischen Zellen?")

    # Burst 3 – Smalltalk Switch (1)
    add(u, 3, "smalltalk", "easy",
        "Ich forsche uebrigens an der Universitaet Muenchen im Bereich Biochemie.",
        plant_fact="greta_university_field")

    # Burst 4 – Physik Hard (5)
    add(u, 4, "physics_hard", "hard",
        "Erklaere den Welle-Teilchen-Dualismus in der Quantenmechanik.")
    add(u, 4, "physics_hard", "hard",
        "Was sagt das Heisenbergsche Unschaerfe-Prinzip aus?")
    add(u, 4, "physics_hard", "hard",
        "Erklaere den Unterschied zwischen starker und schwacher Kernkraft.")
    add(u, 4, "physics_hard", "hard",
        "Was ist der Unterschied zwischen Spezifischer und Molarer Waermekapazitaet?")
    add(u, 4, "physics_hard", "hard",
        "Erklaere den Photoelektrischen Effekt und seine historische Bedeutung.")

    # Burst 5 – Chemie Hard (4)
    add(u, 5, "chemistry_hard", "hard",
        "Was ist der Unterschied zwischen SN1 und SN2-Mechanismus in der organischen Chemie?")
    add(u, 5, "chemistry_hard", "hard",
        "Erklaere das Le-Chatelier-Prinzip mit einem konkreten Beispiel.")
    add(u, 5, "chemistry_hard", "hard",
        "Was ist der Unterschied zwischen Gleichgewichtskonstante Kp und Kc?")
    add(u, 5, "chemistry_hard", "hard",
        "Erklaere das Konzept der Aromatizitaet und die Hueckel-Regel.")

    # Burst 6 – Mathe Switch (3)
    add(u, 6, "math_medium", "medium",
        "Was ist eine Differentialgleichung und wie loest man sie?")
    add(u, 6, "math_medium", "medium",
        "Was ist eine Fourier-Reihe und wofuer wird sie in der Physik benoetigt?")
    add(u, 6, "math_medium", "medium",
        "Was ist der Unterschied zwischen linearer und nichtlinearer Regression?")

    # Burst 7 – Biologie Hard (4)
    add(u, 7, "biology_hard", "hard",
        "Erklaere den Mechanismus der Proteinsynthese: Transkription und Translation.")
    add(u, 7, "biology_hard", "hard",
        "Was ist epigenetische Regulation und welche Rolle spielt sie bei der Genexpression?")
    add(u, 7, "biology_hard", "hard",
        "Erklaere den Mechanismus der Immunantwort: angeborenes vs. adaptives Immunsystem.")
    add(u, 7, "biology_hard", "hard",
        "Was ist CRISPR-Cas9 aus biochemischer Sicht? Erklaere den Schnittmechanismus.")

    # Burst 8 – Memory Recall (1)
    add(u, 8, "memory_recall", "medium",
        "An welcher Universitaet forsche ich und in welchem Bereich?",
        expect_keywords=["muenchen", "biochemie", "forsch"])

    # -----------------------------------------------------------------------
    # User 8: hans_mixed  (25 Fragen)
    # Gelegenheitsnutzer, kurze Bursts, sehr diverse Themen (1-3 pro Burst)
    # -----------------------------------------------------------------------
    u = "hans_mixed"

    # 10 Bursts x 2-3 Fragen = 25 Fragen
    add(u, 1, "greeting", "easy", "Hallo, ich habe mal eine schnelle Frage.")
    add(u, 1, "general", "easy",
        "Was ist der Unterschied zwischen einem Virus und einem Bakterium?")

    add(u, 2, "tech_quick", "easy",
        "Was bedeutet API? Kurze Erklaerung bitte.")
    add(u, 2, "tech_quick", "easy",
        "Was ist der Unterschied zwischen HTTP und HTTPS?")
    add(u, 2, "tech_quick", "medium",
        "Was ist Docker und wofuer wird es eingesetzt? Kurz bitte.")

    add(u, 3, "smalltalk", "easy",
        "Ich heisse Hans und wohne in Hamburg.",
        plant_fact="hans_name_city")
    add(u, 3, "smalltalk", "easy",
        "Was gibt es Interessantes in Hamburg, was man unbedingt gesehen haben muss?",
        expect_keywords=["hamburg", "hafen", "reeperbahn", "elbphilharmonie"])

    add(u, 4, "cooking", "easy",
        "Wie lange kocht man Spaghetti al dente?")
    add(u, 4, "cooking", "easy",
        "Was ist der Unterschied zwischen Espresso und Americano?")

    add(u, 5, "history_quick", "easy",
        "Wer war Leonardo da Vinci – kurze Zusammenfassung.")
    add(u, 5, "history_quick", "medium",
        "Was war die Bedeutung der Gutenberg-Bibel fuer die Gesellschaft?")

    add(u, 6, "science_quick", "easy",
        "Wie entsteht ein Regenbogen?")
    add(u, 6, "science_quick", "medium",
        "Was ist Schwerkraft – einfache Erklaerung fuer ein Kind.")

    add(u, 7, "tech_medium", "medium",
        "Was ist Machine Learning und wie unterscheidet es sich von klassischer Programmierung?")
    add(u, 7, "tech_medium", "medium",
        "Was ist der Unterschied zwischen KI, Machine Learning und Deep Learning?")

    add(u, 8, "memory_recall", "easy",
        "In welcher Stadt wohne ich?",
        expect_keywords=["hamburg"])

    add(u, 9, "current_events", "medium",
        "Welche Vor- und Nachteile hat autonomes Fahren in der Praxis?")
    add(u, 9, "current_events", "medium",
        "Was sind die groessten Herausforderungen der Energiewende in Deutschland?")
    add(u, 9, "current_events", "hard",
        "Erklaere die oekonomischen Folgen des Klimawandels fuer globale Lieferketten.")

    add(u, 10, "math_quick", "easy",
        "Was ist Zinzeszins? Erklaere an einem einfachen Beispiel.")
    add(u, 10, "general", "easy",
        "Was ist der Unterschied zwischen Empathie und Sympathie?")
    add(u, 10, "general", "medium",
        "Welche Vor- und Nachteile hat das bedingungslose Grundeinkommen aus oekonomischer Sicht?")
    add(u, 10, "general", "easy",
        "Was ist der Unterschied zwischen Inland und Ausland aus steuerlicher Sicht – vereinfacht?")
    add(u, 10, "closing", "easy",
        "Danke fuer die Hilfe, das war super!")
    add(u, 10, "memory_recall", "easy",
        "Wie heisse ich nochmal?",
        expect_keywords=["hans"])

    return turns


# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------

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
    payload = json.dumps({
        "session_id": session_id,
        "user_id": user_id,
        "message": message,
        "max_tokens": MAX_TOKENS,
    }).encode("utf-8")

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


# ---------------------------------------------------------------------------
# Audit logic
# ---------------------------------------------------------------------------

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
        recall_ok = any(kw.lower() in resp_lower for kw in spec.expect_keywords)

    return TurnResult(
        turn_index=turn_index,
        user_id=spec.user_id,
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
        required_stages_ok=(
            "accepted" in stages and "orchestration_complete" in stages
        ),
        response_nonempty=len(response) >= 10,
        latency_ok=elapsed_s <= budget,
        recall_ok=recall_ok,
        has_recall_check=bool(spec.expect_keywords),
        error=None,
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _user_session_id(user_id: str) -> str:
    return f"bench200-{user_id}-{uuid.uuid4().hex[:8]}"


def run_benchmark(corpus: List[TurnSpec]) -> list[TurnResult]:
    results: list[TurnResult] = []

    # Group by user, preserving order
    user_order: list[str] = []
    by_user: dict[str, list[tuple[int, TurnSpec]]] = {}
    for i, spec in enumerate(corpus):
        if spec.user_id not in by_user:
            by_user[spec.user_id] = []
            user_order.append(spec.user_id)
        by_user[spec.user_id].append((i, spec))

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

        for idx, spec in user_turns:
            global_turn += 1
            tag = f"[{global_turn:03d}/{len(corpus)}] {user_id} burst={spec.burst} {spec.topic} ({spec.difficulty})"
            print(f"  {tag}")
            print(f"     Q: {spec.message[:90]}{'…' if len(spec.message)>90 else ''}")

            t0 = time.monotonic()
            error: Optional[str] = None
            call_result: dict = {}

            if DRY_RUN:
                call_result = {
                    "progress_stages": ["accepted", "orchestration_started", "orchestration_complete"],
                    "memory_effect_detected": False,
                    "response_text": "[DRY RUN - kein HTTP]",
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
            recall_flag = "" if not spec.expect_keywords else (
                " recall=OK" if tr.recall_ok else " recall=MISS"
            )
            print(f"     [{status}] {elapsed:.1f}s{recall_flag}"
                  + (f" tools={tr.tools_used}" if tr.tools_used else "")
                  + (f" ERR={error}" if error else ""))
            if not tr.passed and not error:
                failures = []
                if not tr.stream_complete:
                    failures.append("stream_incomplete")
                if not tr.required_stages_ok:
                    failures.append(f"stages:{tr.progress_stages}")
                if not tr.response_nonempty:
                    failures.append("empty_response")
                if not tr.latency_ok:
                    failures.append(f"latency>{LATENCY_BUDGET[spec.difficulty]}s")
                if not tr.recall_ok:
                    failures.append(f"recall_miss(expected one of: {spec.expect_keywords})")
                print(f"     └─ {', '.join(failures)}")

    return results


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _build_summary(results: list[TurnResult], started_at: str) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    latencies = [r.elapsed_s for r in results if r.error is None]
    latencies_sorted = sorted(latencies)

    by_difficulty: dict[str, dict] = {}
    for d in ("easy", "medium", "hard"):
        subset = [r for r in results if r.difficulty == d]
        p = sum(1 for r in subset if r.passed)
        by_difficulty[d] = {
            "total": len(subset),
            "passed": p,
            "failed": len(subset) - p,
            "pass_rate_pct": round(p / len(subset) * 100, 1) if subset else 0,
        }

    by_topic: dict[str, dict] = {}
    for r in results:
        if r.topic not in by_topic:
            by_topic[r.topic] = {"total": 0, "passed": 0}
        by_topic[r.topic]["total"] += 1
        if r.passed:
            by_topic[r.topic]["passed"] += 1

    by_user: dict[str, dict] = {}
    for r in results:
        if r.user_id not in by_user:
            by_user[r.user_id] = {"total": 0, "passed": 0, "recall_total": 0, "recall_ok": 0}
        by_user[r.user_id]["total"] += 1
        if r.passed:
            by_user[r.user_id]["passed"] += 1
        if r.has_recall_check:
            by_user[r.user_id]["recall_total"] += 1
            if r.recall_ok:
                by_user[r.user_id]["recall_ok"] += 1

    recall_turns = [r for r in results if r.topic == "memory_recall"]
    recall_passed = sum(1 for r in recall_turns if r.recall_ok)

    memory_effect_turns = sum(1 for r in results if r.memory_effect_detected)

    p95_latency = latencies_sorted[int(0.95 * len(latencies_sorted)) - 1] if latencies_sorted else 0

    return {
        "started_at": started_at,
        "base_url": BASE_URL,
        "dry_run": DRY_RUN,
        "total_turns": total,
        "passed": passed,
        "failed": failed,
        "pass_rate_pct": round(passed / total * 100, 1) if total else 0,
        "avg_latency_s": round(sum(latencies) / len(latencies), 2) if latencies else 0,
        "p95_latency_s": round(p95_latency, 2),
        "max_latency_s": round(max(latencies), 2) if latencies else 0,
        "memory_effect_detected_count": memory_effect_turns,
        "recall_turns": {
            "total": len(recall_turns),
            "recall_ok": recall_passed,
            "recall_rate_pct": round(recall_passed / len(recall_turns) * 100, 1) if recall_turns else 0,
        },
        "by_difficulty": by_difficulty,
        "by_user": by_user,
        "by_topic": by_topic,
        "errors": [
            {"turn": r.turn_index, "user": r.user_id, "error": r.error}
            for r in results if r.error
        ],
    }


def _write_reports(results: list[TurnResult], summary: dict) -> tuple[str, str]:
    os.makedirs(AUDIT_DIR, exist_ok=True)
    jsonl_path = os.path.join(AUDIT_DIR, f"benchmark_200q_{_TS}.jsonl")
    summary_path = os.path.join(AUDIT_DIR, f"benchmark_200q_{_TS}_summary.json")

    with open(jsonl_path, "w", encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    return jsonl_path, summary_path


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    _configure_encoding()
    started_at = datetime.now(timezone.utc).isoformat()

    corpus = _build_corpus()
    total_in_corpus = len(corpus)

    print(f"LIARA 200-Fragen Multi-User Benchmark")
    print(f"  API:       {BASE_URL}")
    print(f"  Turns:     {total_in_corpus}")
    print(f"  Timeout:   {TIMEOUT_S}s")
    print(f"  DryRun:    {DRY_RUN}")
    print(f"  Filter:    {USER_FILTER or 'alle Nutzer'}")
    print()

    if DRY_RUN:
        print("[DRY RUN] Corpus-Auflistung:")
        by_user: dict[str, int] = {}
        for s in corpus:
            by_user[s.user_id] = by_user.get(s.user_id, 0) + 1
        for uid, cnt in by_user.items():
            print(f"  {uid}: {cnt} Fragen")
        print(f"  Gesamt: {total_in_corpus}")
        print()

    results = run_benchmark(corpus)
    summary = _build_summary(results, started_at)

    print(f"\n{'='*60}")
    print("ZUSAMMENFASSUNG")
    print(f"{'='*60}")
    print(f"  Gesamt:          {summary['total_turns']} Turns")
    print(f"  Bestanden:       {summary['passed']} ({summary['pass_rate_pct']}%)")
    print(f"  Fehlgeschlagen:  {summary['failed']}")
    print(f"  Latenz Avg/P95:  {summary['avg_latency_s']}s / {summary['p95_latency_s']}s")
    print(f"  Memory-Recall:   {summary['recall_turns']['recall_ok']}/{summary['recall_turns']['total']} Turns OK ({summary['recall_turns']['recall_rate_pct']}%)")
    print(f"  Memory-Effect:   {summary['memory_effect_detected_count']} Turns mit memory_effect_detected")
    print()
    print("  Nach Schwierigkeitsgrad:")
    for d in ("easy", "medium", "hard"):
        bd = summary["by_difficulty"][d]
        print(f"    {d:8s}: {bd['passed']}/{bd['total']} ({bd['pass_rate_pct']}%)")
    print()
    print("  Nach Nutzer:")
    for uid, bd in summary["by_user"].items():
        recall_info = ""
        if bd["recall_total"] > 0:
            recall_info = f"  recall={bd['recall_ok']}/{bd['recall_total']}"
        print(f"    {uid:20s}: {bd['passed']}/{bd['total']}{recall_info}")

    if not DRY_RUN:
        jsonl_path, summary_path = _write_reports(results, summary)
        print(f"\n  Audit-JSONL:  {jsonl_path}")
        print(f"  Summary-JSON: {summary_path}")

    if summary["errors"]:
        print(f"\n  Fehler ({len(summary['errors'])}):")
        for e in summary["errors"][:10]:
            print(f"    Turn {e['turn']} {e['user']}: {e['error'][:100]}")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
