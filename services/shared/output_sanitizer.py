"""Sanitize user-facing output to prevent internal process leakage.

This component is intentionally conservative and only removes clearly unsafe
phrases/blocks (internal reasoning, prompt dumps, routing/debug traces).
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass
class OutputSanitizationResult:
    text: str
    changed: bool
    removed_fragments: int
    applied_rules: list[str]


class OutputSanitizer:
    """Sanitize text before it is exposed to end users."""

    _INTERNAL_CONTEXT_MARKERS = (
        "SYSTEM_CONTENT",
        "INSTRUCTION",
        "QUERY",
        "CONVERSATION_HISTORY",
        "FACT_CONTEXT",
        "MEMORY_CONTEXT",
        "RELATION_CONTEXT",
        "CHROMA_CONTEXT",
        "EXTERNAL_TOOLS",
        "TONE_AND_STYLE",
        "RESPONSE_PROFILE",
    )

    _MARKER_PATTERN = r"|".join(fr"\[{re.escape(marker)}\]" for marker in _INTERNAL_CONTEXT_MARKERS)

    _LINE_DROP_RULES: list[tuple[str, re.Pattern[str]]] = [
        (
            "internal_thinking_marker",
            re.compile(
                r"(?i)(semantischer\s+gedanke|interner\s+denkprozess|internal\s+reasoning|chain[-\s]?of[-\s]?thought|gedankengang)",
            ),
        ),
        (
            "prompt_leak_marker",
            re.compile(
                rf"(?i)({_MARKER_PATTERN})",
            ),
        ),
        (
            "routing_debug_marker",
            re.compile(
                r"(?i)(routing[-\s]?logik|tool[-\s]?entscheid|prompt[-\s]?logik|debug[_\s-]?run|execution[_\s-]?trace)",
            ),
        ),
    ]

    _MULTI_BLANKS_RE = re.compile(r"\n{3,}")
    _INLINE_MARKER_RE = re.compile(
        r"[ \t]*(?:"
        r"\[(?:SYS|TOOL(?:_RESULT)?|KNOWLEDGE_REFERENCE|KNOWELDGE_REFERENCE|EVIDENCE_CONTEXT)"
        r"(?:\s*:[^\]\r\n]*)?\]"
        r"|【(?:SYS|TOOL(?:_RESULT)?|KNOWLEDGE_REFERENCE|KNOWELDGE_REFERENCE|EVIDENCE_CONTEXT)"
        r"(?:\s*:[^】\r\n]*)?】"
        r"|ã(?:SYS|TOOL(?:_RESULT)?|KNOWLEDGE_REFERENCE|KNOWELDGE_REFERENCE|EVIDENCE_CONTEXT)"
        r"(?:\s*:[^ã\r\n]*)?ã"
        r")[ \t]*",
        re.IGNORECASE,
    )

    def sanitize(self, text: str) -> OutputSanitizationResult:
        """Return sanitized text plus audit metadata."""
        source = text or ""
        if not source:
            return OutputSanitizationResult(text="", changed=False, removed_fragments=0, applied_rules=[])

        lines = source.splitlines()
        kept: list[str] = []
        removed_fragments = 0
        applied_rules: list[str] = []

        for line in lines:
            matched_rule = None
            for rule_name, pattern in self._LINE_DROP_RULES:
                if pattern.search(line):
                    matched_rule = rule_name
                    break
            if matched_rule is not None:
                removed_fragments += 1
                if matched_rule not in applied_rules:
                    applied_rules.append(matched_rule)
                continue
            kept.append(line)

        sanitized = "\n".join(kept).strip()
        inline_marker_removed = False
        if sanitized:
            cleaned = self._INLINE_MARKER_RE.sub(" ", sanitized)
            cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
            cleaned = re.sub(r"\s+\n", "\n", cleaned)
            cleaned = re.sub(r"\n\s+", "\n", cleaned).strip()
            if cleaned != sanitized:
                inline_marker_removed = True
                sanitized = cleaned
        sanitized = self._MULTI_BLANKS_RE.sub("\n\n", sanitized)

        if inline_marker_removed and "inline_source_marker" not in applied_rules:
            applied_rules.append("inline_source_marker")

        if not sanitized:
            sanitized = "The response was withheld because it contained internal-only process details."
            if "fallback_safe_message" not in applied_rules:
                applied_rules.append("fallback_safe_message")

        changed = sanitized != source.strip()
        return OutputSanitizationResult(
            text=sanitized,
            changed=changed,
            removed_fragments=removed_fragments,
            applied_rules=applied_rules,
        )
