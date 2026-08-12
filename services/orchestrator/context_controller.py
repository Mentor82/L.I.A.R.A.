"""Context Control Strategy — per-step budget, adaptive β, and context pressure."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Dict, List

from services.config import Settings


_TOKEN_PATTERN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_SEMANTIC_NORMALIZE = re.compile(r"[^a-z0-9\s]+")


@dataclass
class ControlOutput:
    summary: str
    facts: List[str]
    relations: List[str]
    dropped_items: int
    token_estimate: int
    metadata: Dict[str, object]
    final_context: str
    no_new_information: bool
    meaningful_reduction: bool

    def to_dict(self) -> Dict[str, object]:
        return asdict(self)


class ContextController:
    """Controls per-step context: budget enforcement, adaptive β, pressure, deduplication."""

    PRIORITY = {
        "session": 1,
        "fact": 2,
        "summary": 3,
        "memory": 4,
        "relation": 5,
        "raw": 6,
    }

    def __init__(self) -> None:
        self.max_step_context_tokens = max(256, int(getattr(Settings, "MAX_STEP_CONTEXT_TOKENS", 4000)))
        self.safety_margin_tokens = max(0, int(getattr(Settings, "SAFETY_MARGIN_TOKENS", 1000)))
        self._pressure_ema: float = 0.0  # EMA-smoothed context pressure across steps

    def _compute_pressure(self, current_tokens: int) -> float:
        """Context Pressure P = current_tokens / max_step_context_tokens, clamped to [0, 1]."""
        return min(1.0, current_tokens / max(1, self.max_step_context_tokens))

    def _adaptive_budget(self, reasoning_step: int, p_smoothed: float) -> int:
        """Compute adaptive token budget using β decay and pressure factor.

        β(s) = max(0.5, 1.0 - 0.15 * (s - 1))  -- retention factor per step
        pressure_factor = 1.0 - 0.3 * P          -- shrinks up to 30% under full pressure
        budget = base_budget * β * pressure_factor, floor at 128
        """
        base = self.max_step_context_tokens - self.safety_margin_tokens
        beta = max(0.5, 1.0 - 0.15 * (reasoning_step - 1))
        pressure_factor = 1.0 - (0.3 * p_smoothed)
        return max(128, int(base * beta * pressure_factor))

    def control(
        self,
        *,
        previous_context: str,
        new_context: str,
        reasoning_step: int,
        validation_status: str = "derived",
    ) -> ControlOutput:
        prev_items = self._split_items(previous_context)
        new_items = self._split_items(new_context)
        merged_items = prev_items + new_items

        input_items = len(merged_items)
        if input_items == 0:
            metadata = {
                "source": "compression_layer",
                "compression_level": "step_summary",
                "input_items": 0,
                "output_items": 0,
                "reasoning_step": reasoning_step,
                "validation_status": validation_status,
            }
            return ControlOutput(
                summary="",
                facts=[],
                relations=[],
                dropped_items=0,
                token_estimate=0,
                metadata=metadata,
                final_context="",
                no_new_information=True,
                meaningful_reduction=False,
            )

        deduped = self._dedupe_exact(merged_items)
        semantically_merged = self._dedupe_semantic(deduped)
        prioritized = self._sort_by_priority(semantically_merged)

        # Replacement over accumulation: keep compressed categories over raw fragments.
        has_compressed_categories = any(self._classify(item) != "raw" for item in prioritized)
        if has_compressed_categories:
            prioritized = [item for item in prioritized if self._classify(item) != "raw"]

        # Adaptive β + Context Pressure: replace static budget with step/pressure-aware budget.
        raw_token_count = self._count_tokens("\n".join(merged_items))
        p_raw = self._compute_pressure(raw_token_count)
        self._pressure_ema = 0.4 * p_raw + 0.6 * self._pressure_ema  # EMA α=0.4 for hysteresis
        usable_budget = self._adaptive_budget(reasoning_step, self._pressure_ema)
        bounded = self._apply_budget(prioritized, usable_budget)
        summary = self._build_summary(bounded)

        # Raw-only context must not be forwarded unchanged across reasoning steps.
        if bounded and all(self._classify(item) == "raw" for item in bounded):
            compressed_items: List[str] = []
        else:
            compressed_items = bounded

        output_items = len(compressed_items) + (1 if summary else 0)
        dropped_items = max(0, input_items - len(compressed_items))

        facts = [self._strip_prefix(item) for item in compressed_items if self._classify(item) in {"fact", "memory"}][:6]
        relations = [self._strip_prefix(item) for item in compressed_items if self._classify(item) == "relation"][:6]

        final_lines = []
        if summary:
            final_lines.append(f"[summary] {summary}")
        final_lines.extend(compressed_items)
        final_context = "\n".join(final_lines)
        token_estimate = self._count_tokens(final_context)

        prev_keys = {self._semantic_key(item) for item in prev_items if self._semantic_key(item)}
        effective_new_source = compressed_items if compressed_items else bounded
        new_keys = {self._semantic_key(item) for item in effective_new_source if self._semantic_key(item)}
        no_new_information = len(new_keys - prev_keys) == 0

        input_tokens = self._count_tokens("\n".join(merged_items))
        reduction_ratio = 1.0 - (token_estimate / max(1, input_tokens))
        meaningful_reduction = dropped_items > 0 or reduction_ratio >= 0.10

        metadata = {
            "source": "context_controller",
            "compression_level": "step_control",
            "input_items": input_items,
            "output_items": output_items,
            "reasoning_step": reasoning_step,
            "validation_status": validation_status,
            "usable_context_tokens": usable_budget,
            "pressure_ema": round(self._pressure_ema, 4),
            "beta": round(max(0.5, 1.0 - 0.15 * (reasoning_step - 1)), 4),
            "input_token_estimate": input_tokens,
            "output_token_estimate": token_estimate,
            "reduction_ratio": round(reduction_ratio, 4),
        }

        return ControlOutput(
            summary=summary,
            facts=facts,
            relations=relations,
            dropped_items=dropped_items,
            token_estimate=token_estimate,
            metadata=metadata,
            final_context=final_context,
            no_new_information=no_new_information,
            meaningful_reduction=meaningful_reduction,
        )

    @staticmethod
    def _split_items(text: str) -> List[str]:
        return [line.strip() for line in (text or "").splitlines() if line.strip()]

    @staticmethod
    def _classify(item: str) -> str:
        lower = item.lower()
        if lower.startswith("[session]"):
            return "session"
        if lower.startswith("[fact]"):
            return "fact"
        if lower.startswith("[summary]"):
            return "summary"
        if lower.startswith("[memory]"):
            return "memory"
        if lower.startswith("[relation]"):
            return "relation"
        if lower.startswith("[context]"):
            return "raw"
        return "raw"

    @staticmethod
    def _strip_prefix(item: str) -> str:
        return re.sub(r"^\[[^\]]+\]\s*", "", item).strip()

    @classmethod
    def _semantic_key(cls, item: str) -> str:
        payload = cls._strip_prefix(item).lower()
        payload = _SEMANTIC_NORMALIZE.sub(" ", payload)
        payload = " ".join(payload.split())
        return payload

    @staticmethod
    def _count_tokens(text: str) -> int:
        return len(_TOKEN_PATTERN.findall(text or ""))

    @classmethod
    def _dedupe_exact(cls, items: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for item in items:
            key = item.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    @classmethod
    def _dedupe_semantic(cls, items: List[str]) -> List[str]:
        chosen: Dict[str, str] = {}
        for item in items:
            key = cls._semantic_key(item)
            if not key:
                continue
            prev = chosen.get(key)
            if prev is None:
                chosen[key] = item
                continue
            # Prefer non-raw category and shorter representation.
            prev_type = cls._classify(prev)
            cur_type = cls._classify(item)
            if prev_type == "raw" and cur_type != "raw":
                chosen[key] = item
            elif len(item) < len(prev):
                chosen[key] = item
        return list(chosen.values())

    @classmethod
    def _sort_by_priority(cls, items: List[str]) -> List[str]:
        return sorted(items, key=lambda entry: (cls.PRIORITY.get(cls._classify(entry), 99), len(entry)))

    @classmethod
    def _apply_budget(cls, items: List[str], token_budget: int) -> List[str]:
        out: List[str] = []
        used = 0
        for item in items:
            t = cls._count_tokens(item)
            if out and used + t > token_budget:
                continue
            if not out and t > token_budget:
                out.append(item)
                break
            out.append(item)
            used += t
        return out

    @classmethod
    def _build_summary(cls, items: List[str]) -> str:
        buckets: Dict[str, List[str]] = {
            "session": [],
            "fact": [],
            "summary": [],
            "memory": [],
            "relation": [],
            "raw": [],
        }
        for item in items:
            buckets[cls._classify(item)].append(cls._strip_prefix(item))

        parts: List[str] = []
        if buckets["session"]:
            parts.append(f"Session context: {buckets['session'][0][:140]}")
        if buckets["fact"]:
            parts.append(f"Facts: {', '.join(buckets['fact'][:2])[:180]}")
        if buckets["summary"]:
            parts.append(f"Reasoning summary: {buckets['summary'][0][:160]}")
        if buckets["memory"]:
            parts.append(f"Memory evidence: {buckets['memory'][0][:160]}")
        if buckets["relation"]:
            parts.append(f"Relations: {buckets['relation'][0][:160]}")

        if not parts and buckets["raw"]:
            raw_excerpt = "; ".join(buckets["raw"][:2])[:220]
            parts.append(f"Compressed context: {raw_excerpt}")

        return " | ".join(parts)[:420]

    # Backwards-compatibility: orchestrator calls .compress() — keep working during migration
    compress = control


# Backwards-compatibility aliases (remove after full migration)
CompressionOutput = ControlOutput
ContextCompressor = ContextController
