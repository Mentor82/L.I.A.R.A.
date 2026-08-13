"""Schema migration runner for Governance & Sys-Audit PostgreSQL tables."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("liara.db.schema")

MIGRATIONS_DIR = Path(__file__).parent.parent.parent / "db" / "migrations"


def apply_governance_schema_sync(connection: Any) -> None:
    """Apply SQL migrations for governance, operations, events, and sys_audit tables."""
    migration_file = MIGRATIONS_DIR / "001_governance_and_sys_audit.sql"
    if not migration_file.exists():
        logger.error(f"Migration file not found: {migration_file}")
        raise FileNotFoundError(f"Migration file missing: {migration_file}")

    sql = migration_file.read_text(encoding="utf-8")
    with connection.cursor() as cursor:
        cursor.execute(sql)
    connection.commit()
    logger.info("Successfully applied governance and sys-audit schema migration.")
