"""
Relational Database Adapter for AI-Brain (SQLite/PostgreSQL).
Stores threads, turns, and facts with entity_id, namespace, and timestamp metadata.
"""

import sqlite3
import json
from typing import List, Dict, Any, Optional
from ai_brain.config import SQLITE_DB_PATH
from ai_brain.schema import EpistemicState


class RelationalBrainStore:
    """Manages SQLite / Relational DB storage for facts, threads, and turn transcripts."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = str(db_path or SQLITE_DB_PATH)
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS threads (
                    thread_id TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    title TEXT,
                    created_at REAL,
                    metadata_json TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS turns (
                    turn_id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL,
                    FOREIGN KEY(thread_id) REFERENCES threads(thread_id)
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS facts (
                    fact_id TEXT PRIMARY KEY,
                    entity_id TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    epistemic_state TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_type TEXT NOT NULL,
                    visibility TEXT NOT NULL DEFAULT 'shared',
                    created_at REAL
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_turns_entity ON turns(entity_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_facts_entity ON facts(entity_id, namespace)")
            conn.commit()

    def upsert_thread(self, thread_id: str, entity_id: str, title: str, created_at: float, metadata: Dict[str, Any]) -> None:
        with self._get_connection() as conn:
            conn.cursor().execute(
                """
                INSERT OR REPLACE INTO threads (thread_id, entity_id, title, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, entity_id, title, created_at, json.dumps(metadata)),
            )
            conn.commit()

    def upsert_turn(self, turn_id: str, thread_id: str, entity_id: str, role: str, content: str, created_at: float) -> None:
        with self._get_connection() as conn:
            conn.cursor().execute(
                """
                INSERT OR REPLACE INTO turns (turn_id, thread_id, entity_id, role, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (turn_id, thread_id, entity_id, role, content, created_at),
            )
            conn.commit()

    def upsert_fact(
        self,
        fact_id: str,
        entity_id: str,
        namespace: str,
        subject: str,
        predicate: str,
        obj: str,
        epistemic_state: EpistemicState = EpistemicState.VERIFIED,
        confidence: float = 1.0,
        source_type: str = "conversation",
        visibility: str = "shared",
        created_at: float = 0.0,
    ) -> None:
        with self._get_connection() as conn:
            conn.cursor().execute(
                """
                INSERT OR REPLACE INTO facts 
                (fact_id, entity_id, namespace, subject, predicate, object, epistemic_state, confidence, source_type, visibility, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (fact_id, entity_id, namespace, subject, predicate, obj, epistemic_state.value, confidence, source_type, visibility, created_at),
            )
            conn.commit()

    def query_facts(self, entity_id: str = "nephy", namespace: Optional[str] = None, visibility: str = "shared") -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if namespace:
                rows = cursor.execute(
                    "SELECT * FROM facts WHERE entity_id = ? AND namespace = ? AND visibility = ?",
                    (entity_id, namespace, visibility),
                ).fetchall()
            else:
                rows = cursor.execute(
                    "SELECT * FROM facts WHERE entity_id = ? AND visibility = ?",
                    (entity_id, visibility),
                ).fetchall()
            return [dict(row) for row in rows]
