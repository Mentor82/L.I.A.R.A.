"""Streaming reasoning extractor for Ollama chat/generate responses."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from ollama import AsyncClient


_STEP_PATTERN = re.compile(r"(?im)^\s*(?:[-*+]\s+|\d+[\.)]\s+|step\s*\d+\s*[:.)-])")
_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)


@dataclass
class _PhaseState:
    started_at: float | None = None
    ended_at: float | None = None


class OllamaReasoningStream:
    def __init__(self) -> None:
        self.thinking_text = ""
        self.answer_text = ""
        self.current_phase = "idle"

        self._thinking_phase = _PhaseState()
        self._answer_phase = _PhaseState()
        self._last_event_at: float | None = None
        self._stream_started_at: float = time.perf_counter()

        self.thinking_tokens = 0
        self.answer_tokens = 0

        self._step_hits = 0
        self._bullet_hits = 0
        self._numbered_hits = 0
        self._heading_hits = 0
        self._thinking_chunks = 0
        self._answer_chunks = 0

    def process_chunk(self, chunk: Any) -> Dict[str, Any]:
        data = self._normalize_chunk(chunk)
        now = time.perf_counter()
        self._last_event_at = now

        message = data.get("message") or {}
        thinking_piece = self._safe_text(message.get("thinking") or data.get("thinking"))
        answer_piece = self._safe_text(
            message.get("content")
            or data.get("response")
            or data.get("content")
        )

        phase = "idle"
        if thinking_piece:
            phase = "thinking"
            self.on_thinking(thinking_piece)

        if answer_piece:
            if self._answer_phase.started_at is None:
                self._answer_phase.started_at = now
                if self._thinking_phase.started_at is not None and self._thinking_phase.ended_at is None:
                    self._thinking_phase.ended_at = now
            phase = "answer" if phase == "idle" else "mixed"
            self.on_answer(answer_piece)

        if data.get("done") is True:
            if self._thinking_phase.started_at is not None and self._thinking_phase.ended_at is None:
                self._thinking_phase.ended_at = now
            if self._answer_phase.started_at is not None and self._answer_phase.ended_at is None:
                self._answer_phase.ended_at = now

        self.current_phase = phase

        return {
            "phase": phase,
            "thinking_delta": thinking_piece,
            "answer_delta": answer_piece,
            "thinking_tokens": self.thinking_tokens,
            "answer_tokens": self.answer_tokens,
            "thinking_text": self.thinking_text,
            "answer_text": self.answer_text,
            "done": bool(data.get("done", False)),
        }

    def on_thinking(self, token: str) -> None:
        now = time.perf_counter()
        if self._thinking_phase.started_at is None:
            self._thinking_phase.started_at = now

        self.thinking_text += token
        self.thinking_tokens += self._count_tokens(token)
        self._thinking_chunks += 1
        self._update_structure_metrics(token)

    def on_answer(self, token: str) -> None:
        now = time.perf_counter()
        if self._answer_phase.started_at is None:
            self._answer_phase.started_at = now
        self.answer_text += token
        self.answer_tokens += self._count_tokens(token)
        self._answer_chunks += 1

    def finalize(self) -> Dict[str, Any]:
        now = self._last_event_at or time.perf_counter()
        if self._thinking_phase.started_at is not None and self._thinking_phase.ended_at is None:
            self._thinking_phase.ended_at = now
        if self._answer_phase.started_at is not None and self._answer_phase.ended_at is None:
            self._answer_phase.ended_at = now

        thinking_duration_ms = self._duration_ms(self._thinking_phase)
        answer_duration_ms = self._duration_ms(self._answer_phase)

        rds = self._compute_reasoning_depth_score(
            thinking_tokens=self.thinking_tokens,
            step_hits=self._step_hits,
            bullet_hits=self._bullet_hits,
            numbered_hits=self._numbered_hits,
            thinking_duration_ms=thinking_duration_ms,
        )

        return {
            "thinking": self.thinking_text,
            "answer": self.answer_text,
            "phase": self.current_phase,
            "metrics": {
                "thinking_tokens": self.thinking_tokens,
                "answer_tokens": self.answer_tokens,
                "thinking_phase_duration_ms": thinking_duration_ms,
                "answer_phase_duration_ms": answer_duration_ms,
                "stream_duration_ms": round((now - self._stream_started_at) * 1000.0, 3),
                "thinking_chunks": self._thinking_chunks,
                "answer_chunks": self._answer_chunks,
                "step_hits": self._step_hits,
                "bullet_hits": self._bullet_hits,
                "numbered_hits": self._numbered_hits,
                "heading_hits": self._heading_hits,
                "reasoning_depth_score": rds,
            },
        }

    @staticmethod
    def _safe_text(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        return str(value)

    @staticmethod
    def _normalize_chunk(chunk: Any) -> Dict[str, Any]:
        if isinstance(chunk, dict):
            return chunk
        if isinstance(chunk, (bytes, bytearray)):
            text = chunk.decode("utf-8", errors="ignore").strip()
            return json.loads(text) if text else {}
        if isinstance(chunk, str):
            text = chunk.strip()
            return json.loads(text) if text else {}
        raise TypeError(f"Unsupported chunk type: {type(chunk)!r}")

    @staticmethod
    def _count_tokens(text: str) -> int:
        return len(_TOKEN_PATTERN.findall(text))

    def _update_structure_metrics(self, text: str) -> None:
        matches = _STEP_PATTERN.findall(text)
        self._step_hits += len(matches)

        self._bullet_hits += len(re.findall(r"(?m)^\s*[-*+]\s+", text))
        self._numbered_hits += len(re.findall(r"(?m)^\s*\d+[\.)]\s+", text))
        self._heading_hits += len(re.findall(r"(?im)^\s*(?:step|phase|plan|analysis)\b", text))

    @staticmethod
    def _duration_ms(phase: _PhaseState) -> float:
        if phase.started_at is None or phase.ended_at is None:
            return 0.0
        return round((phase.ended_at - phase.started_at) * 1000.0, 3)

    @staticmethod
    def _compute_reasoning_depth_score(
        *,
        thinking_tokens: int,
        step_hits: int,
        bullet_hits: int,
        numbered_hits: int,
        thinking_duration_ms: float,
    ) -> float:
        token_component = min(40.0, thinking_tokens * 0.20)
        structure_component = min(35.0, step_hits * 4.0 + bullet_hits * 1.5 + numbered_hits * 2.0)
        duration_component = min(25.0, (thinking_duration_ms / 1000.0) * 4.0)
        return round(min(100.0, token_component + structure_component + duration_component), 2)


async def example_chat_stream(client: Any, *, model: str, messages: list[dict[str, str]]) -> Dict[str, Any]:
    stream = OllamaReasoningStream()
    async for chunk in client.chat(model=model, messages=messages, stream=True):
        stream.process_chunk(chunk)
    return stream.finalize()


async def example_generate_stream(
    client: "AsyncClient",
    *,
    model: str,
    prompt: str,
) -> Dict[str, Any]:
    stream = OllamaReasoningStream()
    async for chunk in client.generate(model=model, prompt=prompt, stream=True):
        stream.process_chunk(chunk)
    return stream.finalize()
