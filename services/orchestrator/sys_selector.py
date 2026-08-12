"""Two-stage /sys command selection for the orchestrator.

Stage 1 — needs_sys(query) -> bool
    Decides whether the query even needs a /sys tool call.

Stage 2 — select_sys_command(query) -> SysCommandSelection
    Picks the right concrete command + args and assigns a CommandCategory.

CommandCategory mirrors the classification used in sys_command_policy:
    READ_INSPECT      – cat, ls, grep, head, tail, find (safe, path-restricted)
    COMPUTE_TRANSFORM – julia (arithmetic, conversions, controlled math script via WSL)
    FETCH             – curl (HTTP/S GET only)
    DANGEROUS         – anything else (never auto-selected; blocked at policy layer)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ── Command category taxonomy ────────────────────────────────────────────────

class CommandCategory(str, Enum):
    READ_INSPECT      = "read_inspect"
    COMPUTE_TRANSFORM = "compute_transform"
    FETCH             = "fetch"
    WRITE_MUTATE      = "write_mutate"
    DANGEROUS         = "dangerous"


# Category for every profiled command
COMMAND_CATEGORIES: dict[str, CommandCategory] = {
    "cat":     CommandCategory.READ_INSPECT,
    "ls":      CommandCategory.READ_INSPECT,
    "grep":    CommandCategory.READ_INSPECT,
    "head":    CommandCategory.READ_INSPECT,
    "tail":    CommandCategory.READ_INSPECT,
    "find":    CommandCategory.READ_INSPECT,
    "date":    CommandCategory.READ_INSPECT,
    "time":    CommandCategory.READ_INSPECT,
    "health":  CommandCategory.READ_INSPECT,
    "julia":   CommandCategory.COMPUTE_TRANSFORM,
    "python3": CommandCategory.COMPUTE_TRANSFORM,
    "python":  CommandCategory.COMPUTE_TRANSFORM,
    "curl":    CommandCategory.FETCH,
    "mkdir":   CommandCategory.WRITE_MUTATE,
    "touch":   CommandCategory.WRITE_MUTATE,
    "tee":     CommandCategory.WRITE_MUTATE,
}

# Commands that may be auto-selected from chat
AUTO_SELECTABLE_CATEGORIES: frozenset[CommandCategory] = frozenset([
    CommandCategory.READ_INSPECT,
    CommandCategory.COMPUTE_TRANSFORM,
    CommandCategory.FETCH,
    CommandCategory.WRITE_MUTATE,
])


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SysCommandSelection:
    """Describes the concrete /sys command to run for a chat query."""
    command:  str
    args:     list[str]
    context:  str                        # agent_* tag for executor / normalizer
    intent:   str                        # router intent string (time/python/url_fetch/…)
    category: CommandCategory
    stdin_text: str | None = None
    extra:    dict = field(default_factory=dict)


# ── Keyword tables ────────────────────────────────────────────────────────────

_TIME_KW = frozenset([
    "current", "time", "today", "now",
    "uhrzeit", "zeit", "heute", "datum", "date", "spät", "spaet",
])

_PYTHON_KW = frozenset([
    "berechne", "berechnen", "berechnung",
    "rechne", "rechnen",
    "factorial", "fibonacci", "primzahl", "prime",
    "kehrwert", "potenz", "wurzel", "sqrt",
    "fahrenheit", "celsius", "kelvin", "umrechnen", "umrechnung",
    "byte", "kilobyte", "megabyte", "gigabyte", "terabyte",
    " kb ", " mb ", " gb ", " tb ",
    "calculate", "compute", "evaluate",
    "wie viel ist", "was ist das ergebnis",
    "ergebnis von", "wert von",
])
_PYTHON_RE = re.compile(r"\d+\s*[\+\-\*\/\*\*]\s*\d+")

_WORKSPACE_KW = frozenset([
    "list files", "show files", "zeige dateien", "dateien anzeigen",
    "liste dateien", "was liegt im workspace", "workspace inhalt",
    "read file", "datei lesen", "zeige datei",
])

_WRITE_KW = frozenset([
    "write", "save", "schreibe", "speichere",
    "create file", "erstelle datei", "leere datei",
    "create folder", "create directory", "erstelle ordner", "erstelle verzeichnis",
    "mkdir", "touch",
])

_TEMP_HINT_RE = re.compile(r"\b(?:tmp|temp|temporary|temporar|temporär)\b", re.IGNORECASE)
_WRITE_QUOTED_RE = re.compile(
    r'(?:write|save|schreibe|speichere)\s+["“](?P<content>.+?)["”]\s+(?:to|into|in|nach)\s+(?P<path>[\w./\-]+\.[A-Za-z0-9]+)',
    re.IGNORECASE,
)
_EMPTY_FILE_RE = re.compile(
    r'(?:create|make|erstelle|anlegen|lege\s+an)\s+(?:an\s+)?(?:empty\s+)?(?:temp(?:orary)?\s+)?(?:file|datei)\s+(?P<path>[\w./\-]+\.[A-Za-z0-9]+)',
    re.IGNORECASE,
)
_DIR_RE = re.compile(
    r'(?:create|make|erstelle|anlegen|lege\s+an)\s+(?:a\s+)?(?:temp(?:orary)?\s+)?(?:folder|directory|ordner|verzeichnis)\s+(?P<path>[\w./\-]+)',
    re.IGNORECASE,
)

_WEB_KW = frozenset([
    "what", "how", "who", "search", "find",
    "suche", "finden", "recherch",
    "was ist", "was sind", "wie ist", "wie viel", "wie lautet",
    "welche version", "welches", "wer ist", "wann ist", "wann wurde",
    "aktuell", "aktuelle version", "neueste", "neueste version",
    "zeig mir", "zeige mir", "erkläre", "erklär",
])

_URL_RE = re.compile(r"https?://\S+")


def _normalize_extracted_url(value: str) -> str:
    """Remove prose punctuation captured immediately after an explicit URL."""
    url = str(value or "").rstrip(".,;:!")
    pairs = (("(", ")"), ("[", "]"), ("{", "}"))
    changed = True
    while changed and url:
        changed = False
        for opening, closing in pairs:
            if url.endswith(closing) and url.count(closing) > url.count(opening):
                url = url[:-1]
                changed = True
    return url

# Word-boundary check for time keywords (avoid "dateien" matching "date")
_TIME_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(kw) for kw in _TIME_KW) + r")\b",
    re.IGNORECASE,
)

# Stricter time-intent detector used for selecting the `date` command.
# This avoids routing factual "current" queries (e.g. current president)
# to `date` when web retrieval is the better candidate.
_TIME_INTENT_RE = re.compile(
    r"\b(?:"
    r"what\s+time|current\s+time|time\s+is\s+it|"
    r"wie\s+sp[aä]t|uhrzeit|welche\s+zeit|"
    r"utc\s+time|utc\s+zeit|zeit\s+utc|"
    r"welches\s+datum|heutiges\s+datum|date\s+today|today'?s\s+date|"
    r"aktuelle\s+zeit|aktuelle\s+uhrzeit"
    r")\b",
    re.IGNORECASE,
)

_UTC_HINT_RE = re.compile(r"\butc\b|\bz\b|iso[-\s]?8601", re.IGNORECASE)
_HEALTH_INTENT_RE = re.compile(
    r"\b(?:"
    r"sys\s+health|"
    r"/health|"
    r"backend\s+health|"
    r"system\s*status|"
    r"service\s*status|"
    r"aktueller\s+status|"
    r"aktueller\s+zustand|"
    r"betriebszustand|"
    r"gesundheitszustand|"
    r"wie\s+geht\s+es\s+dir"
    r")\b",
    re.IGNORECASE,
)
_CONTEXT_PREFIX_RE = re.compile(
    r"^\s*\[Context:\s*Focused on Gene Node (?P<component>[^\]]+)\]\s*",
    re.IGNORECASE,
)
_STATUS_WORD_RE = re.compile(
    r"\b(?:status|zustand|health|gesundheit|betriebszustand|verfuegbarkeit|verfügbarkeit)\b",
    re.IGNORECASE,
)
_SELF_OR_INTERNAL_RE = re.compile(
    r"\b(?:dein|deine|dir|du|liara|system|orchestrator|api|memory|embedding|bridge|sys)\b",
    re.IGNORECASE,
)


def _effective_query_text(query: str) -> str:
    """Return user-facing query text without UI context prefix."""
    return _CONTEXT_PREFIX_RE.sub("", query or "", count=1).strip()


def _is_time_intent(query: str) -> bool:
    text = _effective_query_text(query)
    if _TIME_INTENT_RE.search(text):
        return True

    # Accept broader phrasing such as "aktuelle UTC-Zeit" and
    # "ISO-8601" requests even when tokens are hyphenated.
    normalized = text.lower().replace("-", " ")
    has_time_word = bool(
        re.search(r"\b(zeit|uhrzeit|uhr|time|datum|date|now|heute|today|sp[aä]t|spaet)\b", normalized)
    )
    has_utc_or_iso = bool(_UTC_HINT_RE.search(normalized))
    has_iso_only_phrase = bool(re.search(r"\biso\s*8601\b", normalized))
    if has_iso_only_phrase and has_utc_or_iso:
        return True
    return has_time_word and has_utc_or_iso


def _is_health_intent(query: str) -> bool:
    raw_text = query or ""
    text = _effective_query_text(raw_text)
    if _HEALTH_INTENT_RE.search(raw_text) or _HEALTH_INTENT_RE.search(text):
        return True

    context_match = _CONTEXT_PREFIX_RE.match(raw_text)
    has_status_word = bool(_STATUS_WORD_RE.search(text))
    has_internal_target = bool(context_match or _SELF_OR_INTERNAL_RE.search(text))
    return has_status_word and has_internal_target


# ── Stage 1: needs_sys ────────────────────────────────────────────────────────

def needs_sys(query: str) -> bool:
    """Return True if the query warrants a /sys tool call."""
    effective_query = _effective_query_text(query)
    q = effective_query.lower()
    return bool(
        _is_health_intent(query)
        or
        _TIME_RE.search(q)
        or any(kw in q for kw in _PYTHON_KW)
        or _PYTHON_RE.search(q)
        or _URL_RE.search(effective_query)
        or any(kw in q for kw in _WORKSPACE_KW)
        or any(kw in q for kw in _WRITE_KW)
        or any(kw in q for kw in _WEB_KW)
    )


# ── Stage 2: select_sys_command ───────────────────────────────────────────────

def select_sys_command(query: str, inference_invoker: Optional[object] = None) -> SysCommandSelection:
    """Pick the concrete /sys command for a chat query.

    Call this only when needs_sys() returned True.
    
    Args:
        query: The user's chat query.
        inference_invoker: Optional inference service for LLM-based parameter extraction.
                          If provided, enables fallback to LLM for write-intent extraction.
    
    Returns:
        SysCommandSelection with command, args, context, intent, and category.
    """
    effective_query = _effective_query_text(query)
    q = effective_query.lower()

    # --- Health/status queries ---------------------------------------------
    if _is_health_intent(query):
        return SysCommandSelection(
            command="health",
            args=[],
            context="agent_health_check",
            intent="health",
            category=CommandCategory.READ_INSPECT,
        )

    # --- Date/Time queries (strict intent only) -----------------------------
    if _is_time_intent(effective_query):
        wants_utc = bool(_UTC_HINT_RE.search(effective_query or ""))
        args = ["-u", "+%Y-%m-%dT%H:%M:%SZ"] if wants_utc else ["+%Y-%m-%d %H:%M:%S %Z"]
        return SysCommandSelection(
            command="date",
            args=args,
            context="agent_datetime_fetch",
            intent="time",
            category=CommandCategory.READ_INSPECT,
        )

    # --- Julia compute / transform ------------------------------------------
    if any(kw in q for kw in _PYTHON_KW) or _PYTHON_RE.search(q):
        return SysCommandSelection(
            command="julia",
            args=[],           # args handled by the executor via staged Julia model
            context="agent_julia_exec",
            intent="julia",
            category=CommandCategory.COMPUTE_TRANSFORM,
        )

    # --- Explicit URL fetch --------------------------------------------------
    url_m = _URL_RE.search(effective_query)
    if url_m:
        url = _normalize_extracted_url(url_m.group())
        return SysCommandSelection(
            command="curl",
            args=["-s", "-L", "-m", "15",
                  "-A", "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
                  url],
            context="agent_url_fetch",
            intent="url_fetch",
            category=CommandCategory.FETCH,
            extra={"url": url},
        )

    # --- Controlled write flows ---------------------------------------------
    write_match = _WRITE_QUOTED_RE.search(effective_query)
    if write_match:
        prefer_temp = bool(_TEMP_HINT_RE.search(effective_query))
        target_path = _resolve_managed_target(write_match.group("path"), prefer_temp=prefer_temp)
        storage_scope = "temp" if target_path.startswith("/home/liara/temp") else "workspace"
        return SysCommandSelection(
            command="tee",
            args=[target_path],
            context="agent_workspace_write",
            intent="workspace_write",
            category=CommandCategory.WRITE_MUTATE,
            stdin_text=write_match.group("content"),
            extra={"target_path": target_path, "storage_scope": storage_scope, "write_mode": "overwrite"},
        )

    empty_file_match = _EMPTY_FILE_RE.search(effective_query)
    if empty_file_match:
        prefer_temp = bool(_TEMP_HINT_RE.search(effective_query))
        target_path = _resolve_managed_target(empty_file_match.group("path"), prefer_temp=prefer_temp)
        storage_scope = "temp" if target_path.startswith("/home/liara/temp") else "workspace"
        return SysCommandSelection(
            command="touch",
            args=[target_path],
            context="agent_workspace_touch",
            intent="workspace_write",
            category=CommandCategory.WRITE_MUTATE,
            extra={"target_path": target_path, "storage_scope": storage_scope, "write_mode": "touch"},
        )

    dir_match = _DIR_RE.search(effective_query)
    if dir_match:
        prefer_temp = bool(_TEMP_HINT_RE.search(effective_query))
        target_path = _resolve_managed_target(dir_match.group("path"), prefer_temp=prefer_temp)
        storage_scope = "temp" if target_path.startswith("/home/liara/temp") else "workspace"
        return SysCommandSelection(
            command="mkdir",
            args=["-p", target_path],
            context="agent_workspace_mkdir",
            intent="workspace_write",
            category=CommandCategory.WRITE_MUTATE,
            extra={"target_path": target_path, "storage_scope": storage_scope, "write_mode": "mkdir"},
        )

    # --- Workspace file ops --------------------------------------------------
    if any(kw in q for kw in _WORKSPACE_KW):
        file_m = re.search(r"[\w\-\.]+\.\w+", effective_query)
        if file_m and "read" in q:
            return SysCommandSelection(
                command="cat",
                args=[f"/home/liara/workspace/{file_m.group()}"],
                context="agent_workspace_read",
                intent="workspace",
                category=CommandCategory.READ_INSPECT,
            )
        return SysCommandSelection(
            command="find",
            args=["/home/liara/workspace", "-maxdepth", "2", "-type", "f"],
            context="agent_workspace_list",
            intent="workspace",
            category=CommandCategory.READ_INSPECT,
        )

    # --- Ubuntu release lookup (special fetch) -----------------------------
    _UBUNTU_VERSION_KW = frozenset(["version", "stable", "lts", "aktuell", "current"])
    if "ubuntu" in q and any(kw in q for kw in _UBUNTU_VERSION_KW):
        return SysCommandSelection(
            command="curl",
            args=["-s", "-L", "-m", "8",
                  "https://changelogs.ubuntu.com/meta-release-lts"],
            context="agent_ubuntu_release_lookup",
            intent="web",
            category=CommandCategory.FETCH,
        )

    # --- LLM-based write-intent extraction (fallback for natural-language writes) ---
    # If inference_invoker is available AND we detected write keywords,
    # try LLM extraction before falling back to web search
    if inference_invoker is not None and any(kw in q for kw in _WRITE_KW):
        from services.orchestrator.write_intent_extractor import (
            extract_write_intent_parameters,
            resolve_managed_target_from_extracted,
        )
        
        extracted = extract_write_intent_parameters(effective_query, inference_invoker)
        if extracted and extracted.get("target_path"):
            target_path = resolve_managed_target_from_extracted(
                extracted["target_path"],
                extracted.get("storage_scope", "workspace"),
            )
            write_mode = extracted.get("write_mode", "overwrite")
            content = extracted.get("content")
            
            # Dispatch to appropriate command based on write_mode
            if write_mode == "mkdir":
                return SysCommandSelection(
                    command="mkdir",
                    args=["-p", target_path],
                    context="agent_workspace_mkdir",
                    intent="workspace_write",
                    category=CommandCategory.WRITE_MUTATE,
                    extra={
                        "target_path": target_path,
                        "storage_scope": extracted.get("storage_scope", "workspace"),
                        "write_mode": "mkdir",
                    },
                )
            elif write_mode == "touch":
                return SysCommandSelection(
                    command="touch",
                    args=[target_path],
                    context="agent_workspace_touch",
                    intent="workspace_write",
                    category=CommandCategory.WRITE_MUTATE,
                    extra={
                        "target_path": target_path,
                        "storage_scope": extracted.get("storage_scope", "workspace"),
                        "write_mode": "touch",
                    },
                )
            elif write_mode in {"overwrite", "append"} and content:
                return SysCommandSelection(
                    command="tee",
                    args=[target_path],
                    context="agent_workspace_write",
                    intent="workspace_write",
                    category=CommandCategory.WRITE_MUTATE,
                    stdin_text=content,
                    extra={
                        "target_path": target_path,
                        "storage_scope": extracted.get("storage_scope", "workspace"),
                        "write_mode": write_mode,
                    },
                )

    # --- Web lookup (Wikipedia via curl) ------------------------------------
    safe_query = re.sub(r"[^\w\s\-]", "", effective_query).strip().replace(" ", "+")
    return SysCommandSelection(
        command="curl",
        args=[
            "-s", "-L", "-m", "15",
            "-A", "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
            f"https://en.wikipedia.org/wiki/Special:Search/{safe_query}",
        ],
        context="agent_web_lookup",
        intent="web",
        category=CommandCategory.FETCH,
        extra={"search_query": safe_query},
    )


def _resolve_managed_target(path_fragment: str, *, prefer_temp: bool) -> str:
    path_fragment = path_fragment.strip().strip('"').strip("'")
    if path_fragment.startswith("/home/liara/temp/") or path_fragment == "/home/liara/temp":
        return path_fragment
    if path_fragment.startswith("/home/liara/workspace/"):
        return path_fragment
    base = "/home/liara/temp" if prefer_temp else "/home/liara/workspace"
    return f"{base}/{path_fragment.lstrip('/')}"
