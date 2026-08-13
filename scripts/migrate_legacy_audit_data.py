"""Idempotent legacy audit & governance data migration script.

Imports existing sys_governance_proposals.json, sys_governance_events.jsonl, and sys_audit.jsonl
into PostgreSQL canonical tables. Writes `legacy_import_completed = true` to system_metadata table.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from services.config.settings import Settings
from services.memory.tier_store import FactStore
from services.api.storage.schema_runner import apply_governance_schema_sync
from services.api.storage.governance_repository import redact_and_bound_payload

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("liara.migration")


def run_legacy_audit_migration(postgres_url: str | None = None) -> dict[str, int]:
    """Run idempotent migration of legacy JSON/JSONL files into PostgreSQL."""
    url = postgres_url or Settings.POSTGRES_URL
    if not url:
        logger.error("POSTGRES_URL is not configured.")
        raise ValueError("POSTGRES_URL is missing.")

    fact_store = FactStore(postgres_url=url, auto_initialize=False)
    fact_store._initialize_sync()
    conn = fact_store._pool.getconn()

    counts = {
        "proposals_imported": 0,
        "proposals_skipped": 0,
        "gov_events_imported": 0,
        "gov_events_skipped": 0,
        "sys_audit_imported": 0,
        "sys_audit_skipped": 0,
    }

    try:
        # 1. Apply Schema
        apply_governance_schema_sync(conn)

        with conn.cursor() as cur:
            # Check if migration already completed
            cur.execute("SELECT value FROM system_metadata WHERE key = 'legacy_import_completed';")
            row = cur.fetchone()
            if row and row[0] and row[0].get("completed") is True:
                logger.info("Legacy import already completed previously. Skipping file import.")
                return counts

            # 2. Migrate Proposals (sys_governance_proposals.json)
            workspace_dir = Path(os.getenv("LIARA_SYS_GOVERNANCE_PATH", str(PROJECT_ROOT / "data"))).parent
            proposals_file = workspace_dir / "sys_governance_proposals.json"
            if proposals_file.exists():
                try:
                    data = json.loads(proposals_file.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        for prop_id, prop in data.items():
                            if not isinstance(prop, dict):
                                continue
                            cur.execute("SELECT 1 FROM governance_proposals WHERE proposal_id = %s;", (prop_id,))
                            if cur.fetchone():
                                counts["proposals_skipped"] += 1
                                continue

                            cur.execute(
                                """
                                INSERT INTO governance_proposals (
                                    proposal_id, revision, decision, state, tool_name, command,
                                    parameters, policy_check, capability, rationale, requested_by,
                                    decided_by, decision_reason, decision_at, traceability, created_at, updated_at
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                ON CONFLICT (proposal_id) DO NOTHING;
                                """,
                                (
                                    prop_id,
                                    int(prop.get("revision") or 1),
                                    str(prop.get("decision") or "pending"),
                                    str(prop.get("state") or ("decided" if prop.get("decision") != "pending" else "created")),
                                    str(prop.get("tool_name") or "sys"),
                                    str(prop.get("command") or ""),
                                    json.dumps(redact_and_bound_payload(prop.get("parameters"))),
                                    json.dumps(redact_and_bound_payload(prop.get("policy_check"))),
                                    prop.get("capability"),
                                    prop.get("rationale"),
                                    prop.get("requested_by"),
                                    prop.get("decided_by"),
                                    prop.get("decision_reason"),
                                    prop.get("decision_at"),
                                    json.dumps(redact_and_bound_payload(prop.get("traceability"))),
                                    prop.get("created_at", datetime.now(UTC).isoformat()),
                                    prop.get("updated_at", datetime.now(UTC).isoformat()),
                                ),
                            )
                            counts["proposals_imported"] += 1
                except Exception as exc:
                    logger.warning(f"Error parsing proposals file: {exc}")

            # 3. Migrate Governance Events (sys_governance_events.jsonl)
            events_file = workspace_dir / "sys_governance_events.jsonl"
            if events_file.exists():
                try:
                    for line in events_file.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            continue
                        evt_id = str(event.get("event_id") or f"evt-{hash(line)}")
                        prop_id = str(event.get("proposal_id") or "")
                        if not prop_id:
                            continue

                        cur.execute("SELECT 1 FROM governance_events WHERE event_id = %s;", (evt_id,))
                        if cur.fetchone():
                            counts["gov_events_skipped"] += 1
                            continue

                        cur.execute(
                            """
                            INSERT INTO governance_events (
                                event_id, proposal_id, proposal_revision, event_type, decision, state, actor_id, traceability, created_at
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT DO NOTHING;
                            """,
                            (
                                evt_id,
                                prop_id,
                                int(event.get("proposal_revision") or 1),
                                str(event.get("event_type") or "proposal_event"),
                                event.get("decision"),
                                event.get("state"),
                                event.get("actor_id") or event.get("decided_by"),
                                json.dumps(redact_and_bound_payload(event.get("traceability"))),
                                event.get("timestamp") or datetime.now(UTC).isoformat(),
                            ),
                        )
                        counts["gov_events_imported"] += 1
                except Exception as exc:
                    logger.warning(f"Error parsing governance events file: {exc}")

            # 4. Migrate Sys Audit Events (sys_audit.jsonl)
            audit_file = workspace_dir / "sys_audit.jsonl"
            if audit_file.exists():
                try:
                    for line in audit_file.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        item = json.loads(line)
                        if not isinstance(item, dict):
                            continue
                        evt_id = str(item.get("event_id") or f"aud-{hash(line)}")
                        tool = str(item.get("tool_name") or "unknown")

                        cur.execute("SELECT 1 FROM sys_audit_events WHERE event_id = %s;", (evt_id,))
                        if cur.fetchone():
                            counts["sys_audit_skipped"] += 1
                            continue

                        cur.execute(
                            """
                            INSERT INTO sys_audit_events (
                                event_id, operation_id, proposal_id, request_id, session_id, run_id,
                                tool_name, lifecycle_stage, outcome, actor_id, context, metadata, timestamp
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT DO NOTHING;
                            """,
                            (
                                evt_id,
                                item.get("operation_id"),
                                item.get("proposal_id"),
                                item.get("request_id"),
                                item.get("session_id"),
                                item.get("run_id"),
                                tool,
                                str(item.get("lifecycle_stage") or "completed"),
                                item.get("outcome") or item.get("decision"),
                                item.get("actor_id"),
                                item.get("context"),
                                json.dumps(redact_and_bound_payload(item.get("metadata") or item)),
                                item.get("timestamp") or datetime.now(UTC).isoformat(),
                            ),
                        )
                        counts["sys_audit_imported"] += 1
                except Exception as exc:
                    logger.warning(f"Error parsing sys_audit file: {exc}")

            # 5. Set persistent cutover marker
            now_iso = datetime.now(UTC).isoformat()
            cur.execute(
                """
                INSERT INTO system_metadata (key, value, updated_at)
                VALUES ('legacy_import_completed', %s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at;
                """,
                (json.dumps({"completed": True, "migrated_at": now_iso, "counts": counts}), now_iso),
            )
            conn.commit()
            logger.info(f"Migration completed cleanly. Summary: {counts}")

    finally:
        fact_store._pool.putconn(conn)

    return counts


if __name__ == "__main__":
    run_legacy_audit_migration()
