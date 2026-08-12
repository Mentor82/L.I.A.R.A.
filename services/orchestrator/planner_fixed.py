"""Planner stage for orchestrator split.

Owns prompt construction from query and tool outputs.
"""

import json
import re
import unicodedata

from services.contracts import PlannerPlan, PlannerRequest

# Common German function words that are unambiguous enough as language signals.
# Checked as whole-word tokens to avoid false positives.
_GERMAN_STOPWORDS = frozenset(
    [
        "ich", "du", "er", "sie", "es", "wir", "ihr",
        "ist", "sind", "war", "wird", "werden", "habe", "haben", "hat",
        "der", "die", "das", "den", "dem", "des", "ein", "eine", "eines",
        "und", "oder", "aber", "nicht", "kein", "keine",
        "was", "wie", "wann", "wo", "wer", "warum", "welche", "welcher", "welches",
        "bitte", "danke", "nein", "ja",
        "auf", "mit", "von", "bei", "nach", "aus", "über", "unter", "vor",
        "aktuell", "aktuelle", "aktuellen", "neu", "neue", "neuen",
        "kann", "kannst", "könnte", "koennte", "soll", "sollte", "muss", "müssen", "muessten",
        "gibt", "geben", "zeig", "zeige", "erkläre", "erklär", "erklaere", "erklaer",
        # ASCII-transcribed German (ue/ae/oe substitutions)
        "heisst", "weiss", "strasse", "moeglich", "moegliche", "koennen",
        "saetzen", "satz", "praesident", "fuer", "ueber", "waere",
        "erklaerung", "beschreibe", "beschreibung", "nochmal", "kurz", "bitte",
    ]
)

_GERMAN_CHARS = frozenset("äöüÄÖÜß")

# Regex-based signal for common ASCII transcriptions of German umlaut/Eszett words.
# This detects forms like "fuer", "ueber", "koennen", "heisst", "strasse".
_GERMAN_ASCII_WORD_PATTERN = re.compile(
    r"\b(?:"
    r"fuer|ueber|gegenueber|zurueck|"
    r"koennen|koennte|koennte|moeglich|moeglichkeit|"
    r"waere|haette|haetten|gaebe|"
    r"heisst|weiss|strasse|grosse|groesser|"
    r"erklaer|erklaere|erklaerung|"
    r"praesident|saetze|saetzen"
    r")\b",
    flags=re.IGNORECASE,
)


def _detect_language(text: str) -> str:
    """Return 'German' or 'English' based on lightweight heuristics.

    Priority order:
    1. Explicit preference override ("bitte auf deutsch", "in english please")
    2. German-specific Unicode characters (ä/ö/ü/ß) — strong signal
    3. Regex-based ASCII German orthography (ae/oe/ue/ss words)
    4. German stopword density — moderate fallback signal
    """
    lower = text.lower()

    # --- explicit overrides ---
    if any(tok in lower for tok in ("bitte auf deutsch", "bitte in deutsch", "auf deutsch", "deutsch")):
        return "German"
    if any(tok in lower for tok in ("in english", "please in english", "respond in english", "answer in english")):
        return "English"

    # --- Unicode character signal ---
    german_char_count = sum(1 for ch in text if ch in _GERMAN_CHARS)
    if german_char_count >= 1:
        return "German"

    # --- ASCII German orthography signal ---
    if _GERMAN_ASCII_WORD_PATTERN.search(text):
        return "German"

    # --- stopword density fallback ---
    tokens = re.findall(r"[a-zäöüß]+", lower)
    if tokens:
        hits = sum(1 for t in tokens if t in _GERMAN_STOPWORDS)
        if hits / len(tokens) >= 0.15:  # ≥15 % German stopwords  →  German
            return "German"

    return "English"


class QueryPlanner:
    """Builds LLM input prompt from current execution context."""

    GENERIC_STYLE_PHRASES = (
        "it seems",
        "based on the information you provided earlier",
        "according to the information you provided earlier",
        "how can i assist you further",
        "is there anything specific you would like",
    )

    def build_plan(self, request: PlannerRequest) -> PlannerPlan:
        # Tool outputs with mandatory citations
        tool_section = ""
        for tool_name, output in request.tool_outputs.items():
            rendered_output = self._render_tool_output(output)
            tool_section += f"\n[TOOL: {tool_name}]\n{rendered_output[:900]}\n"

        # Ordered context priority (routing-aware):
        # FACT_LOOKUP: Facts → Memory → Relations → Working context
        # SESSION_RECALL: Conversation history → Facts → Memory
        # SEMANTIC_MEMORY: Memory → Relations → Facts → Working context
        # NONE (default): History → Facts → Memory → Relations → Working context
        history_section = request.conversation_history.strip()
        fact_section = request.fact_context.strip()
        memory_section = request.memory_context.strip()
        relation_section = request.relation_context.strip()
        working_section = (request.working_context or request.context_documents).strip()
        response_profile = self._infer_response_profile(request.query, history_section)
        style_instruction = self._build_style_instruction(response_profile)

        # Use routing-aware instruction generation based on Librarian route (primary_context_kind)
        instruction = self._build_routing_aware_instruction(
            primary_context_kind=request.primary_context_kind,
            has_external_context=bool(tool_section or fact_section or memory_section or relation_section or working_section),
        )

        prompt = f"""
[RESPONSE_PROFILE]
Language: {response_profile['language']}
Tone: {response_profile['tone']}
Format: {response_profile['format']}
Length: {response_profile['length']}
Context Strategy: {response_profile['context_strategy']}

[TONE_AND_STYLE]
{style_instruction}

[CONVERSATION_HISTORY]
{history_section or '(none)'}

[FACT_CONTEXT]
Stable facts from fact storage:
{fact_section or '(none)'}

[MEMORY_CONTEXT]
Semantic memory retrieved for this query:
{memory_section or '(none)'}

[RELATION_CONTEXT]
Structural relation evidence:
{relation_section or '(none)'}

[CHROMA_CONTEXT]
Scope-filtered short-term context from this run:
{working_section or '(none)'}

[EXTERNAL_TOOLS]
{tool_section or '(none)'}

[QUERY]
{request.query}

[INSTRUCTION]
{instruction}
""".strip()

        return PlannerPlan(
            prompt=prompt,
            metadata={
                "tool_count": len(request.tools_used),
                "language": response_profile["language"],
                "tone": response_profile["tone"],
                "format": response_profile["format"],
                "primary_context_kind": request.primary_context_kind,
            },
        )

    @staticmethod
    def _render_tool_output(output: object) -> str:
        if isinstance(output, dict):
            summary_text = str(output.get("summary_text") or "").strip()
            if summary_text:
                return summary_text
            try:
                return json.dumps(output, ensure_ascii=False)
            except TypeError:
                return str(output)
        return str(output)

    def _infer_response_profile(self, query: str, history_section: str) -> dict[str, str]:
        signal_text = f"{history_section}\n{query}"

        language = _detect_language(signal_text)

        response_format = "essay" if self._wants_essay(signal_text) else "structured prose"
        if self._wants_list(signal_text):
            response_format = "bullet list"

        length = "medium"
        if self._wants_long_form(signal_text):
            length = "long"
        elif self._wants_brief(signal_text):
            length = "short"

        tone = "natural, specific, non-generic"
        if self._wants_formal(signal_text):
            tone = "formal, precise, non-generic"

        context_strategy = (
            "Prioritize explicit user preferences from recent conversation history, then short-term context, then tool evidence."
        )

        return {
            "language": language,
            "tone": tone,
            "format": response_format,
            "length": length,
            "context_strategy": context_strategy,
        }

    def _build_style_instruction(self, profile: dict[str, str]) -> str:
        banned = ", ".join(f'"{phrase}"' for phrase in self.GENERIC_STYLE_PHRASES)
        return (
            f"Write in {profile['language']}. "
            f"Use a {profile['tone']} voice. "
            f"Prefer {profile['format']} and target a {profile['length']} answer length. "
            "Be concrete and grounded in the user's request and prior turns. "
            "Avoid generic assistant filler, classroom-summary clichés, and repetitive meta-commentary. "
            f"Do not use phrases such as {banned} unless the user explicitly asks for that style. "
            "When prior conversation contains preferences such as language, style, or requested level of detail, carry them forward explicitly."
        )

    def _build_routing_aware_instruction(self, *, primary_context_kind: str, has_external_context: bool) -> str:
        """Build LLM instruction with routing-aware context prioritization."""
        base_citation = (
            "MANDATORY: Every factual claim from [TOOL], [FACT_CONTEXT], [MEMORY_CONTEXT], [RELATION_CONTEXT], "
            "or [CHROMA_CONTEXT] MUST be followed by [KNOWLEDGE_REFERENCE].\n"
            "Do NOT add [KNOWLEDGE_REFERENCE] for [CONVERSATION_HISTORY]. Never invent facts or citations."
        )
        
        if primary_context_kind == "FACT_LOOKUP":
            return (
                "FACT_LOOKUP MODE: Prioritize [FACT_CONTEXT] first (personal facts, system facts), "
                "then [MEMORY_CONTEXT], then external sources.\n" + base_citation
            )
        elif primary_context_kind == "SESSION_RECALL":
            return (
                "SESSION_RECALL MODE: Prioritize [CONVERSATION_HISTORY] first, referencing specific prior turns. "
                "Then use [FACT_CONTEXT], [MEMORY_CONTEXT].\n" + base_citation + "\nNever invent history."
            )
        elif primary_context_kind == "SEMANTIC_MEMORY":
            return (
                "SEMANTIC_MEMORY MODE: Prioritize [MEMORY_CONTEXT] and [RELATION_CONTEXT] first for thematic connections, "
                "then [FACT_CONTEXT], then external sources.\n" + base_citation
            )
        elif not has_external_context:
            return (
                "Answer using conversation history accurately. Reference prior turns directly if user asks what was discussed. "
                "Do not invent sources."
            )
        else:
            return (
                "Balanced mode: Use all context sources in order: "
                "[CONVERSATION_HISTORY] → [FACT_CONTEXT] → [MEMORY_CONTEXT] → [RELATION_CONTEXT] → external.\n" + base_citation
            )

    @staticmethod
    def _wants_essay(text: str) -> bool:
        t = text.lower()
        return any(token in t for token in ("aufsatz", "essay", "fließtext", "fliesstext"))

    @staticmethod
    def _wants_list(text: str) -> bool:
        t = text.lower()
        return any(token in t for token in ("stichpunkte", "bullet", "liste", "auflisten"))

    @staticmethod
    def _wants_long_form(text: str) -> bool:
        t = text.lower()
        return bool(re.search(r"\b(400|500|600|700|800|900|1000)\b", t)) or any(
            token in t for token in ("ausführlich", "ausfuehrlich", "detailliert", "lang", "ca 500", "ca. 500")
        )

    @staticmethod
    def _wants_brief(text: str) -> bool:
        t = text.lower()
        return any(token in t for token in ("kurz", "knapp", "in kurzform", "tl;dr", "tldr"))

    @staticmethod
    def _wants_formal(text: str) -> bool:
        t = text.lower()
        return any(token in t for token in ("formal", "sachlich", "präzise", "praezise", "professionell"))
