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

# Category order is no longer used to pick a winner among competing matches
# (see select_wsl_session_action) -- kept as a stable iteration order only.
_KEYWORD_TABLE: tuple[tuple[str, frozenset[str]], ...] = (
    ("destroy", _DESTROY_KW),
    ("validate", _VALIDATE_KW),
    ("collect", _COLLECT_KW),
    ("status", _STATUS_KW),
    ("create", _CREATE_KW),
    ("plan", _PLAN_KW),
)

SESSION_SCOPED_ACTIONS: frozenset[str] = frozenset({"status", "collect", "validate", "destroy"})

# Bilingual negation markers. A keyword match in the same clause as one of
# these (German commonly negates *after* the verb, e.g. "lösche nicht") is
# treated as not matched at all, rather than trusting bare substring
# presence. This matters most for "destroy": a query like "don't destroy it,
# just show status" must never classify as destroy.
_NEGATION_MARKERS = (
    "don't", "do not", "dont", "not ", "never",
    "nicht", "kein ", "keine ", "ohne", "without",
)

# Negation is scoped to the clause containing the match (split on sentence-
# /clause-level punctuation) rather than a raw character window -- a fixed
# window bleeds across short clauses (e.g. "nicht löschen, nur Status" would
# otherwise let "nicht" negate the unrelated "Status" match too, since both
# sit within a small window of each other).
_CLAUSE_BOUNDARY_RE = re.compile(r"[,.;!?]")


def _is_negated(text: str, match_start: int, match_end: int) -> bool:
    clause_start = 0
    for boundary in _CLAUSE_BOUNDARY_RE.finditer(text):
        if boundary.start() < match_start:
            clause_start = boundary.start() + 1
        else:
            break
    boundary_after = _CLAUSE_BOUNDARY_RE.search(text, match_end)
    clause_end = boundary_after.start() if boundary_after else len(text)
    clause = text[clause_start:clause_end]
    return any(marker in clause for marker in _NEGATION_MARKERS)


def select_wsl_session_action(query: str) -> WslSessionActionSelection:
    """Classify free-text intent into a wsl_session lifecycle action.

    Falls back to "plan" (read-only, no session_id needed, already an
    allow-eligible action in the pre-action Judge profile) in three cases:
    empty/ambiguous input with no keyword match at all; a keyword match that
    is negated (e.g. "don't destroy"); or genuinely competing, non-negated
    matches across more than one action category -- in that last case this
    deliberately does NOT fall back to picking by category precedence, since
    a priority order could silently prefer "destroy" over a co-occurring
    "status" mention. "plan" is the only action always safe to run blind.
    """
    text = (query or "").lower()
    session_match = _SESSION_ID_RE.search(query or "")
    explicit_session_id = session_match.group(0) if session_match else None

    matched: dict[str, str] = {}
    for action, keywords in _KEYWORD_TABLE:
        if action in matched:
            continue
        for keyword in keywords:
            idx = text.find(keyword)
            if idx == -1:
                continue
            if _is_negated(text, idx, idx + len(keyword)):
                continue
            matched[action] = keyword
            break

    if len(matched) == 1:
        (action, keyword), = matched.items()
        return WslSessionActionSelection(action, keyword, explicit_session_id)

    # Zero matches (ambiguous/empty) or multiple competing categories: fail
    # closed to the one action that's always safe to run without confirmation.
    return WslSessionActionSelection("plan", None, explicit_session_id)
