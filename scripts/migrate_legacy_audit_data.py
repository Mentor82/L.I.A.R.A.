"""Idempotent legacy audit & governance data migration script.

Imports existing sys_governance_proposals.json, sys_governance_events.jsonl, and sys_audit.jsonl
into PostgreSQL canonical tables. Writes `legacy_import_completed = true` to system_metadata table.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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


def _deterministic_legacy_id(prefix: str, line: str) -> str:
    """Content-addressed fallback ID for a legacy record without its own event_id.

    Deterministic across process restarts, unlike Python's salted hash() —
    a legacy record lacking event_id would otherwise get a different
    fallback id on every run, defeating ON CONFLICT DO NOTHING dedup and
    creating duplicate rows on every rerun.
    """
    digest = hashlib.sha256(line.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-legacy-{digest}"


# Same sensitive-keyword intent as redact_and_bound_payload's dict-key
# redaction (services/api/storage/governance_repository.py's
# _SENSITIVE_KEY_PATTERN uses the same word list, unanchored), applied as a
# regex over raw text instead: a malformed line isn't valid JSON, so it can't
# be redacted key-by-key. No \b word boundaries around the keyword -- an
# anchored \btoken\b would miss "auth_token"/"apiKey"-style identifiers,
# under-redacting exactly the case that matters.
_SENSITIVE_TEXT_PATTERN = re.compile(
    r'(?i)("?\S*(?:token|secret|password|passwd|key|auth|bearer)\S*"?\s*[:=]\s*)("(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|\S+)'
)
# "Authorization: Bearer <token>"-style values have no key[:=]value separator
# for the token itself -- handled separately.
_BEARER_SCHEME_PATTERN = re.compile(r"(?i)\b(bearer)\s+\S+")
_QUARANTINE_PREVIEW_MAX_CHARS = 500


def _sanitize_quarantine_preview(line: str) -> str:
    """Best-effort redaction of a raw, unparseable line before it's persisted.

    Bearer-scheme redaction runs *first*: "Authorization: Bearer <token>"
    otherwise matches _SENSITIVE_TEXT_PATTERN's key[:=]value shape too (its
    "value" being the literal word "Bearer"), consuming just that word and
    leaving the actual token after it untouched.
    """
    redacted = _BEARER_SCHEME_PATTERN.sub(r"\1 [REDACTED]", line)
    redacted = _SENSITIVE_TEXT_PATTERN.sub(r"\1[REDACTED]", redacted)
    if len(redacted) > _QUARANTINE_PREVIEW_MAX_CHARS:
        redacted = redacted[:_QUARANTINE_PREVIEW_MAX_CHARS] + "...[TRUNCATED]"
    return redacted


def _quarantine_line(source_file: Path, line: str, reason: str) -> None:
    """Append a malformed line's sanitized preview + reason to a quarantine file.

    Keeps a malformed line from silently vanishing (or aborting every
    subsequent line in the same file) while still letting the migration
    report an observable quarantined count. Never stores the raw line
    verbatim -- sys_audit.jsonl entries in particular may carry sensitive
    command/stdin content, so a corrupted line gets a sha256 (for
    correlation/dedup) plus a redacted, length-bounded preview instead.
    """
    quarantine_file = source_file.with_name(source_file.name + ".quarantine.jsonl")
    record = {
        "reason": reason,
        "raw_line_sha256": hashlib.sha256(line.encode("utf-8")).hexdigest(),
        "raw_line_length": len(line),
        "sanitized_preview": _sanitize_quarantine_preview(line),
        "quarantined_at": datetime.now(UTC).isoformat(),
    }
    with quarantine_file.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


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
        "gov_events_quarantined": 0,
        "sys_audit_imported": 0,
        "sys_audit_skipped": 0,
        "sys_audit_quarantined": 0,
    }

    try:
        # 1. Apply Schema
        apply_governance_schema_sync(conn)

        with conn.cursor() as cur:
            # Note: no early-return on a prior "legacy_import_completed" flag.
            # Once Postgres is the canonical store, no new writes should land in
            # the legacy JSONL files, but if a straggler ever does, skipping all
            # file scanning forever would silently drop it. Every run rescans;
            # the per-row ON CONFLICT DO NOTHING / SELECT-then-INSERT checks
            # below make reruns cheap, and the system_metadata marker at the end
            # is updated with the latest counts/timestamp on every run instead
            # of being a one-shot flag.

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
                for line in events_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                        if not isinstance(event, dict):
                            raise ValueError("governance event line is not a JSON object")
                        prop_id = str(event.get("proposal_id") or "")
                        if not prop_id:
                            raise ValueError("governance event line is missing proposal_id")
                    except (json.JSONDecodeError, ValueError) as exc:
                        _quarantine_line(events_file, line, str(exc))
                        counts["gov_events_quarantined"] += 1
                        continue

                    evt_id = str(event.get("event_id") or _deterministic_legacy_id("evt", line))

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

            # 4. Migrate Sys Audit Events (sys_audit.jsonl)
            audit_file = workspace_dir / "sys_audit.jsonl"
            if audit_file.exists():
                for line in audit_file.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        item = json.loads(line)
                        if not isinstance(item, dict):
                            raise ValueError("sys_audit line is not a JSON object")
                    except json.JSONDecodeError as exc:
                        _quarantine_line(audit_file, line, str(exc))
                        counts["sys_audit_quarantined"] += 1
                        continue
                    except ValueError as exc:
                        _quarantine_line(audit_file, line, str(exc))
                        counts["sys_audit_quarantined"] += 1
                        continue

                    evt_id = str(item.get("event_id") or _deterministic_legacy_id("aud", line))
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
