"""Schema migration runner for Governance & Sys-Audit PostgreSQL tables."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("liara.db.schema")

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "db" / "migrations"


def apply_governance_schema_sync(connection: Any) -> None:
    """Apply every NNN_description.sql migration under MIGRATIONS_DIR, in order.

    Tracks applied migrations in a `schema_migrations` table so reruns only
    execute files that have not been recorded yet, rather than either (a)
    hardcoding a single filename or (b) blindly re-executing every file on
    every call and relying on each migration being idempotent forever. Each
    migration's SQL and its `schema_migrations` bookkeeping row commit
    together, so a failed migration is never marked applied.
    """
    if not MIGRATIONS_DIR.is_dir():
        logger.error(f"Migrations directory not found: {MIGRATIONS_DIR}")
        raise FileNotFoundError(f"Migrations directory missing: {MIGRATIONS_DIR}")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        cursor.execute("SELECT filename FROM schema_migrations;")
        already_applied = {row[0] for row in cursor.fetchall()}
    connection.commit()

    migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    if not migration_files:
        logger.error(f"No migration files found in: {MIGRATIONS_DIR}")
        raise FileNotFoundError(f"No migration files in: {MIGRATIONS_DIR}")

    applied_count = 0
    for migration_file in migration_files:
        if migration_file.name in already_applied:
            continue
        sql = migration_file.read_text(encoding="utf-8")
        try:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                cursor.execute(
                    "INSERT INTO schema_migrations (filename) VALUES (%s);",
                    (migration_file.name,),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            logger.exception(f"Migration failed, rolled back: {migration_file.name}")
            raise
        applied_count += 1
        logger.info(f"Applied migration: {migration_file.name}")

    if applied_count:
        logger.info(f"Applied {applied_count} new migration(s) out of {len(migration_files)} total.")
    else:
        logger.info(f"Schema up to date, no new migrations ({len(migration_files)} total already applied).")
