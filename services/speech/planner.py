"""Deterministic semantic planning between LIARA's DDNA and TTS inference."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from services.contracts import SpeechPlan, SpeechPlanSegment, SpeechProsody, VoiceIdentity


_DEFAULT_MAX_CHARS = 70
_VOICE_IDENTITY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "ddna" / "liara-voice-identity.json"
)
_LIST_PREFIX = re.compile(r"^\s*(?:[-*\u2022]+|\d+[.)])\s+")
_TOOL_MARKER = re.compile(r"\[TOOL:[^\]]+\]", flags=re.IGNORECASE)


@lru_cache(maxsize=1)
def load_liara_voice_identity() -> VoiceIdentity:
    return VoiceIdentity.model_validate_json(_VOICE_IDENTITY_PATH.read_text(encoding="utf-8"))


class SpeechPlanner:
    def __init__(self, voice_identity: VoiceIdentity | None = None, *, max_chars: int = _DEFAULT_MAX_CHARS):
        self.voice_identity = voice_identity or load_liara_voice_identity()
        self.max_chars = max_chars

    def plan(self, text: str) -> SpeechPlan:
        segments: list[SpeechPlanSegment] = []
        paragraphs = [block for block in re.split(r"\n\s*\n", text) if block.strip()]
        for paragraph in paragraphs:
            paragraph_segments: list[SpeechPlanSegment] = []
            lines = [line for line in paragraph.splitlines() if line.strip()]
            for raw_line in lines:
                role, cleaned = self._classify_and_clean(raw_line)
                if not cleaned:
                    continue
                clauses = [
                    part.strip()
                    for part in re.split(r"(?<=[.!?])\s+", cleaned)
                    if part.strip()
                ]
                for clause in clauses:
                    paragraph_segments.extend(self._plan_clause(clause, role))
            if paragraph_segments:
                last = paragraph_segments[-1]
                paragraph_segments[-1] = last.model_copy(
                    update={"pause_after_ms": max(last.pause_after_ms, 320)}
                )
                segments.extend(paragraph_segments)

        if not segments:
            raise ValueError("Speech text is empty after normalization")
        return SpeechPlan(voice_identity=self.voice_identity, segments=segments)

    def split_segment(self, segment: SpeechPlanSegment, *, max_chars: int) -> list[SpeechPlanSegment]:
        pieces = self._split_text(segment.text, max_chars=max_chars)
        if len(pieces) == 1:
            return [segment]
        return [
            segment.model_copy(
                update={
                    "text": piece,
                    "pause_after_ms": segment.pause_after_ms if index == len(pieces) - 1 else 80,
                }
            )
            for index, piece in enumerate(pieces)
        ]

    def _classify_and_clean(self, raw_line: str) -> tuple[str, str]:
        stripped = raw_line.strip()
        if _LIST_PREFIX.match(stripped):
            role = "list_item"
            stripped = _LIST_PREFIX.sub("", stripped)
        elif stripped.startswith(">"):
            role = "quote"
            stripped = stripped[1:].strip()
        else:
            role = "sentence"
        stripped = _TOOL_MARKER.sub("", stripped)
        stripped = stripped.replace("**", "").replace("__", "").strip()
        stripped = re.sub(r"\s+", " ", stripped)
        return role, re.sub(r"\s+([,.;:!?])", r"\1", stripped)

    def _plan_clause(self, text: str, role: str) -> list[SpeechPlanSegment]:
        pause_after_ms = 160
        prosody = SpeechProsody()
        if role == "list_item":
            pause_after_ms = 220
            prosody = SpeechProsody(emphasis="moderate")
        elif role == "quote":
            pause_after_ms = 260
            prosody = SpeechProsody(pace="measured", tone="quoted")
        elif text.endswith("?"):
            pause_after_ms = 190
            prosody = SpeechProsody(tone="inquisitive")
        elif text.endswith("!"):
            pause_after_ms = 200
            prosody = SpeechProsody(emphasis="moderate")

        pieces = self._split_text(text, max_chars=self.max_chars)
        return [
            SpeechPlanSegment(
                text=piece,
                semantic_role=role,
                pause_after_ms=pause_after_ms if index == len(pieces) - 1 else 80,
                prosody=prosody,
            )
            for index, piece in enumerate(pieces)
        ]

    @staticmethod
    def _split_text(text: str, *, max_chars: int) -> list[str]:
        remaining = text.strip()
        pieces: list[str] = []
        while len(remaining) > max_chars:
            split_at = remaining.rfind(" ", 0, max_chars + 1)
            if split_at <= 0:
                split_at = max_chars
            piece = remaining[:split_at].strip()
            if piece:
                pieces.append(piece)
            remaining = remaining[split_at:].strip()
        if remaining:
            pieces.append(remaining)
        return pieces