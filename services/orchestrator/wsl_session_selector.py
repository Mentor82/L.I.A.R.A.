"""Heuristic action classification for the wsl_session tool.

Mirrors sys_selector.py's keyword-table style: select_wsl_session_action(query)
derives a concrete lifecycle `action` from free-text intent, since no LLM
function-calling layer exists in this pipeline (see sys_selector.py's
select_sys_command for the established precedent).
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WslSessionActionSelection:
    """Describes the classified wsl_session action for a chat query."""
    action: str
    matched_keyword: str | None
    explicit_session_id: str | None  # session_id literally present in the query text


# ── Keyword tables (bilingual DE/EN) ─────────────────────────────────────────

_SESSION_ID_RE = re.compile(r"\bsess-[0-9a-f]{6,32}\b", re.IGNORECASE)

_DESTROY_KW = frozenset([
    "destroy", "delete session", "remove session", "terminate", "tear down",
    "teardown", "clean up", "cleanup", "stop session", "kill session",
    "loesche", "lösche", "entferne session", "beende session",
    "zerstoere", "zerstöre", "sitzung loeschen", "sitzung löschen",
    "sitzung beenden", "raeume auf", "räume auf", "aufraeumen", "aufräumen",
])
_VALIDATE_KW = frozenset([
    "validate", "validation", "verify", "check validity",
    "validiere", "validieren", "pruefe die sitzung", "prüfe die sitzung",
    "ueberpruefe", "überprüfe",
])
_COLLECT_KW = frozenset([
    "collect", "gather", "collect artifacts", "collect results",
    "sammle", "sammeln", "artefakte sammeln", "ergebnisse sammeln",
])
_STATUS_KW = frozenset([
    "status", "check status", "session status", "how is",
    "zustand", "wie ist der status", "sitzungsstatus",
])
_CREATE_KW = frozenset([
    "create", "start session", "new session", "spin up", "set up session",
    "erstelle", "erstellen", "starte", "neue sitzung", "neue session",
    "initialisiere", "richte ein",
])
_PLAN_KW = frozenset([
    "plan", "preview", "dry run", "what would happen", "show plan",
    "plane", "planen", "zeige plan", "vorschau",
])

# Checked in this precedence order: destroy first (narrow, high-consequence
# phrases minimize false positives from other categories' broader vocabulary),
# plan/create last (lowest-risk, safest to fall back into).
_KEYWORD_TABLE: tuple[tuple[str, frozenset[str]], ...] = (
    ("destroy", _DESTROY_KW),
    ("validate", _VALIDATE_KW),
    ("collect", _COLLECT_KW),
    ("status", _STATUS_KW),
    ("create", _CREATE_KW),
    ("plan", _PLAN_KW),
)

SESSION_SCOPED_ACTIONS: frozenset[str] = frozenset({"status", "collect", "validate", "destroy"})


def select_wsl_session_action(query: str) -> WslSessionActionSelection:
    """Classify free-text intent into a wsl_session lifecycle action.

    Falls back to "plan" (read-only, no session_id needed, already an
    allow-eligible action in the pre-action Judge profile) when intent is
    ambiguous or empty -- the only action always safe to run blind.
    """
    text = (query or "").lower()
    session_match = _SESSION_ID_RE.search(query or "")
    explicit_session_id = session_match.group(0) if session_match else None

    for action, keywords in _KEYWORD_TABLE:
        for keyword in keywords:
            if keyword in text:
                return WslSessionActionSelection(action, keyword, explicit_session_id)

    return WslSessionActionSelection("plan", None, explicit_session_id)
