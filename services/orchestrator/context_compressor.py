"""Context Compression — long-conversation history summarization without information loss.

This is conceptually distinct from the Context Control Strategy (context_controller.py):

  context_controller.py  — per-step budget, adaptive β, pressure, deduplication (runs every step)
  context_compressor.py  — periodic squashing of long conversation history (runs when history grows)

Analogy: context_controller = traffic control; context_compressor = highway on-ramp merge.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, List, Optional


@dataclass
class CompressionResult:
    """Output of a long-history compression pass."""
    compressed_history: str          # The squashed history text to use going forward
    original_turn_count: int         # How many turns were in the input
    retained_turn_count: int         # How many turns (or summaries thereof) remain
    token_estimate: int              # Estimated token count of compressed_history
    dropped_turns: int               # Turns that were fully dropped (content absorbed into summary)
    summary_blocks: List[str]        # One summary block per compressed window
    metadata: Dict[str, object]

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class ContextCompressor:
    """Compresses long conversation history by windowed summarization.

    Strategy:
    - Split history into windows of `window_size` turns
    - Each window older than `keep_recent_turns` is collapsed into a single summary block
    - The most recent `keep_recent_turns` turns are kept verbatim
    - Summary blocks preserve: topics, decisions, stated facts, open questions

    This mirrors how Copilot handles long chat contexts: older turns are squashed
    into a lossless summary, recent turns stay intact for immediate context.
    """

    DEFAULT_WINDOW_SIZE = 10        # turns per summary window
    DEFAULT_KEEP_RECENT = 6         # keep last N turns verbatim
    DEFAULT_MAX_SUMMARY_TOKENS = 180  # max tokens per summary block

    def __init__(
        self,
        window_size: int = DEFAULT_WINDOW_SIZE,
        keep_recent_turns: int = DEFAULT_KEEP_RECENT,
        max_summary_tokens: int = DEFAULT_MAX_SUMMARY_TOKENS,
    ) -> None:
        self.window_size = window_size
        self.keep_recent_turns = keep_recent_turns
        self.max_summary_tokens = max_summary_tokens

    def should_compress(self, history: str, threshold_turns: int = 20) -> bool:
        """Return True when history is long enough to warrant compression."""
        turns = self._split_turns(history)
        return len(turns) >= threshold_turns

    def compress(self, history: str, session_id: Optional[str] = None) -> CompressionResult:
        """Compress conversation history via windowed summarization.

        Args:
            history: Full conversation history as newline-separated turns.
            session_id: Optional session identifier for metadata.

        Returns:
            CompressionResult with compressed_history ready to use as context.
        """
        turns = self._split_turns(history)
        original_count = len(turns)

        if original_count <= self.keep_recent_turns:
            # Nothing to compress — history fits in recent window
            return CompressionResult(
                compressed_history=history,
                original_turn_count=original_count,
                retained_turn_count=original_count,
                token_estimate=self._count_tokens(history),
                dropped_turns=0,
                summary_blocks=[],
                metadata={
                    "source": "context_compressor",
                    "action": "no_op",
                    "session_id": session_id or "",
                    "original_turns": original_count,
                },
            )

        recent_turns = turns[-self.keep_recent_turns:]
        older_turns = turns[:-self.keep_recent_turns]

        # Collapse older turns into windowed summary blocks
        summary_blocks: List[str] = []
        dropped = 0
        for i in range(0, len(older_turns), self.window_size):
            window = older_turns[i : i + self.window_size]
            block = self._summarize_window(window)
            summary_blocks.append(block)
            dropped += max(0, len(window) - 1)  # window collapses to 1 summary block

        compressed_parts: List[str] = []
        if summary_blocks:
            compressed_parts.append("[compressed_history]")
            compressed_parts.extend(summary_blocks)
        compressed_parts.extend(recent_turns)

        compressed_history = "\n".join(compressed_parts)
        token_estimate = self._count_tokens(compressed_history)

        return CompressionResult(
            compressed_history=compressed_history,
            original_turn_count=original_count,
            retained_turn_count=len(recent_turns) + len(summary_blocks),
            token_estimate=token_estimate,
            dropped_turns=dropped,
            summary_blocks=summary_blocks,
            metadata={
                "source": "context_compressor",
                "action": "windowed_summary",
                "session_id": session_id or "",
                "original_turns": original_count,
                "summary_blocks": len(summary_blocks),
                "recent_turns_kept": len(recent_turns),
                "dropped_turns": dropped,
                "output_token_estimate": token_estimate,
            },
        )

    def _summarize_window(self, turns: List[str]) -> str:
        """Collapse a window of turns into a single [summary] block.

        NOTE: This is currently a deterministic extractive summary.
        Replace with an LLM-backed abstractive summary call for higher quality.
        """
        if not turns:
            return ""
        # Extractive: keep first sentence of each turn, truncated to budget
        excerpts: List[str] = []
        budget = self.max_summary_tokens
        for turn in turns:
            clean = turn.strip()
            if not clean:
                continue
            first_sentence = clean.split(".")[0][:100].strip()
            if first_sentence:
                excerpts.append(first_sentence)
                budget -= self._count_tokens(first_sentence)
            if budget <= 0:
                break

        joined = "; ".join(excerpts)[:self.max_summary_tokens * 5]
        return f"[summary] {joined}"

    @staticmethod
    def _split_turns(history: str) -> List[str]:
        return [line.strip() for line in (history or "").splitlines() if line.strip()]

    @staticmethod
    def _count_tokens(text: str) -> int:
        import re
        return len(re.findall(r"\w+|[^\w\s]", text or "", re.UNICODE))
