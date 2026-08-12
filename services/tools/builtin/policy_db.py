"""Generic local SQLite policy backend for sys tools.

Each command uses three SQLite files under its own folder in `db/`:
- `db/<command>/w.db` -> whitelist entries
- `db/<command>/g.db` -> greylist entries (requires contextual checks)
- `db/<command>/b.db` -> blacklist entries
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_VALID_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_LEGACY_DB_FILE = re.compile(r"^([A-Za-z0-9_.-]+)\.(w|g|b)\.db$")
_TIERS = ("w", "g", "b")


@dataclass(frozen=True)
class CommandPolicy:
    whitelist: frozenset[str]
    greylist: frozenset[str]
    blacklist: frozenset[str]


def get_policy_db_dir() -> Path:
    """Resolve policy db directory.

    Uses LIARA_POLICY_DB_DIR if provided, otherwise `<repo>/db`.
    """
    env_dir = os.getenv("LIARA_POLICY_DB_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(__file__).resolve().parents[3] / "db"


def load_command_policy(
    command: str,
    defaults: dict[str, Iterable[str]] | None = None,
) -> CommandPolicy:
    """Ensure policy DBs exist and return loaded W/G/B sets for `command`."""
    _validate_name(command)
    defaults = defaults or {}
    db_dir = get_policy_db_dir()
    db_dir.mkdir(parents=True, exist_ok=True)

    loaded: dict[str, frozenset[str]] = {}
    for tier in _TIERS:
        db_path = _policy_db_path(command=command, tier=tier, db_dir=db_dir)
        with sqlite3.connect(db_path) as conn:
            _ensure_schema(conn)
            _seed_if_empty(conn, _normalize_entries(defaults.get(tier, ())))
            loaded[tier] = frozenset(_read_enabled_entries(conn))

    return CommandPolicy(
        whitelist=loaded["w"],
        greylist=loaded["g"],
        blacklist=loaded["b"],
    )


def list_policy_commands(db_dir: Path | None = None) -> frozenset[str]:
    """Return command names that have policy DB entries.

    Supports both layouts:
    - New:    db/<command>/(w|g|b).db
    - Legacy: db/<command>.(w|g|b).db
    """
    root = db_dir or get_policy_db_dir()
    if not root.exists():
        return frozenset()

    commands: set[str] = set()

    for entry in root.iterdir():
        if entry.is_dir() and _VALID_NAME.fullmatch(entry.name):
            if any((entry / f"{tier}.db").exists() for tier in _TIERS):
                commands.add(entry.name)
            continue

        if not entry.is_file():
            continue

        m = _LEGACY_DB_FILE.fullmatch(entry.name)
        if m:
            commands.add(m.group(1))

    return frozenset(commands)


def _policy_db_path(command: str, tier: str, db_dir: Path) -> Path:
    if tier not in _TIERS:
        raise ValueError(f"Unsupported policy tier '{tier}'")

    command_dir = db_dir / command
    command_dir.mkdir(parents=True, exist_ok=True)

    new_path = command_dir / f"{tier}.db"
    _migrate_legacy_db_if_present(command=command, tier=tier, db_dir=db_dir, target_path=new_path)
    return new_path


def _migrate_legacy_db_if_present(command: str, tier: str, db_dir: Path, target_path: Path) -> None:
    """Move legacy flat DB files to the new per-command folder layout.

    Legacy path: `db/<command>.<tier>.db`
    New path:    `db/<command>/<tier>.db`
    """
    if target_path.exists():
        return

    legacy_path = db_dir / f"{command}.{tier}.db"
    if not legacy_path.exists():
        return

    shutil.move(str(legacy_path), str(target_path))


def _validate_name(name: str) -> None:
    if not _VALID_NAME.fullmatch(name):
        raise ValueError(f"Invalid policy name '{name}'")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS entries (
            value TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            note TEXT DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def _seed_if_empty(conn: sqlite3.Connection, entries: list[str]) -> None:
    row = conn.execute("SELECT COUNT(*) FROM entries").fetchone()
    count = int(row[0]) if row else 0
    if count != 0 or not entries:
        return

    conn.executemany(
        "INSERT OR IGNORE INTO entries(value, enabled, note) VALUES (?, 1, 'seeded')",
        [(entry,) for entry in entries],
    )
    conn.commit()


def _read_enabled_entries(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT value FROM entries WHERE enabled = 1 ORDER BY value ASC"
    ).fetchall()
    return [str(r[0]) for r in rows]


def _normalize_entries(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
