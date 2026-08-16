"""Evidence Engine for structured, source-grounded analysis.

This module intentionally does not generate final user wording.
It only builds an evidence package consumed by the answer layer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from services.contracts.evidence_state import EvidenceAssertion, merge_evidence_assertions


FACTUAL_QUERY_RE = re.compile(
    r"\b(why|how|diagnose|analysis|compare|difference|current|latest|quelle|quellen|warum|wieso|analyse|vergleich|aktuell)\b",
    re.IGNORECASE,
)
DESIGN_QUERY_RE = re.compile(
    r"\b(system\s+entwerfen|entwerfen|design|architektur|algorithm(?:us|m)?|wrapper|simd|llvm|jit|global\s+interpreter\s+lock|gil|pyjulia|ctypes|cross[-\s]?language|performance|optimier|policy\s+konfig|konfigurier)\b",
    re.IGNORECASE,
)
CREATIVE_QUERY_RE = re.compile(
    r"\b(poem|story|creative|brainstorm|rewrite|umschreiben|kreativ|geschichte|witz)\b",
    re.IGNORECASE,
)
CASUAL_QUERY_RE = re.compile(
    r"\b(hi|hello|hey|moin|hallo|wie geht|how are you)\b",
    re.IGNORECASE,
)
TIME_SCOPE_RE = re.compile(r"\b(today|latest|current|now|heute|aktuell|derzeit|recent)\b", re.IGNORECASE)


@dataclass
class EvidenceItem:
    source: str
    summary: str
    quality: str = "plausible"  # verified|plausible|weak|conflicting|insufficient
    relevance: float = 0.5
    freshness: float = 0.5
    consistent: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence_state: str | None = None  # EvidenceState.value, e.g. "not_found_in_search" (Issue #8)


@dataclass
class EvidenceEngineResult:
    mode: str
    normalized_query: str
    subquestions: list[str]
    topic_tags: list[str]
    time_scope: str
    required_evidence_level: str
    selected_sources: list[str]
    skipped_sources: list[str]
    selection_reasoning: list[str]
    evidence_items: list[dict[str, Any]]
    fetch_outcomes: dict[str, str]
    retrieval_status: str
    semantic_filtered_evidence: list[dict[str, Any]]
    semantic_discarded_evidence: list[dict[str, Any]]
    validated_evidence: list[dict[str, Any]]
    discarded_evidence: list[dict[str, Any]]
    evidence_quality: str
    confidence_score: float
    conflicts: list[dict[str, Any]]
    resolution_status: str
    unresolved_conflicts_count: int
    evidence_context: str
    facts_block: list[str]
    context_block: list[str]
    conflict_block: list[str]
    answerability: str
    evidence_states: list[dict[str, Any]] = field(default_factory=list)


class EvidenceEngine:
    """Strict evidence processing pipeline.

    Responsibilities:
    - Query decomposition
    - Source selection
    - Evidence collection
    - Evidence validation
    - Conflict detection/resolution status
    - Token-efficient context packaging
    """

    def __init__(
        self,
        reasoning_steps: int | None = None,
        max_items_per_source: int | None = None,
    ) -> None:
        from services.config.settings import Settings
        self.reasoning_steps: int = reasoning_steps if reasoning_steps is not None else getattr(Settings, "EVIDENCE_REASONING_STEPS", 3)
        self.max_items_per_source: int = max_items_per_source if max_items_per_source is not None else getattr(Settings, "EVIDENCE_MAX_ITEMS_PER_SOURCE", 6)
        self.confidence_strong_threshold: float = float(getattr(Settings, "EVIDENCE_CONFIDENCE_STRONG_THRESHOLD", 0.85))
        self.confidence_medium_threshold: float = float(getattr(Settings, "EVIDENCE_CONFIDENCE_MEDIUM_THRESHOLD", 0.70))
        self.semantic_filter_enabled: bool = bool(getattr(Settings, "EVIDENCE_SEMANTIC_FILTER_ENABLED", True))
        self.semantic_min_relevance: float = float(getattr(Settings, "EVIDENCE_SEMANTIC_MIN_RELEVANCE", 0.18))
        # Keep thresholds sane and ordered even with malformed env values.
        if self.confidence_strong_threshold < 0.0:
            self.confidence_strong_threshold = 0.0
        if self.confidence_medium_threshold < 0.0:
            self.confidence_medium_threshold = 0.0
        if self.confidence_medium_threshold > self.confidence_strong_threshold:
            self.confidence_medium_threshold = self.confidence_strong_threshold
        if self.semantic_min_relevance < 0.0:
            self.semantic_min_relevance = 0.0
        if self.semantic_min_relevance > 1.0:
            self.semantic_min_relevance = 1.0

    def analyze(
        self,
        *,
        query: str,
        context_channels: dict[str, str],
        source_counts: dict[str, int],
        tool_outputs: dict[str, Any],
        conversation_history: str,
    ) -> EvidenceEngineResult:
        decomposition = self._decompose_query(query)
        selection = self._select_sources(
            query=query,
            required_level=decomposition["required_evidence_level"],
            source_counts=source_counts,
            tool_outputs=tool_outputs,
            has_history=bool(conversation_history.strip()),
        )
        collection = self._collect_evidence(
            context_channels=context_channels,
            tool_outputs=tool_outputs,
            selected_sources=selection["selected_sources"],
            query=query,
        )
        semantic = self._semantic_filter_evidence(
            evidence_items=collection["evidence_items"],
            normalized_query=decomposition["normalized_query"],
        )
        validation = self._validate_evidence(semantic["filtered_evidence"], decomposition["required_evidence_level"])
        conflicts = self._resolve_conflicts(validation["validated_evidence"])
        packaging = self._package_context(
            normalized_query=decomposition["normalized_query"],
            selected_sources=selection["selected_sources"],
            validated_evidence=validation["validated_evidence"],
            conflicts=conflicts["conflicts"],
            confidence_score=validation["confidence_score"],
            answerability=self._derive_answerability(
                retrieval_status=collection["retrieval_status"],
                evidence_quality=validation["evidence_quality"],
                unresolved_conflicts=conflicts["unresolved_conflicts_count"],
                required_level=decomposition["required_evidence_level"],
            ),
        )

        return EvidenceEngineResult(
            mode="evidence",
            normalized_query=decomposition["normalized_query"],
            subquestions=decomposition["subquestions"],
            topic_tags=decomposition["topic_tags"],
            time_scope=decomposition["time_scope"],
            required_evidence_level=decomposition["required_evidence_level"],
            selected_sources=selection["selected_sources"],
            skipped_sources=selection["skipped_sources"],
            selection_reasoning=selection["selection_reasoning"],
            evidence_items=[self._item_to_dict(item) for item in collection["evidence_items"]],
            fetch_outcomes=collection["fetch_outcomes"],
            retrieval_status=collection["retrieval_status"],
            semantic_filtered_evidence=[self._item_to_dict(item) for item in semantic["filtered_evidence"]],
            semantic_discarded_evidence=[self._item_to_dict(item) for item in semantic["discarded_evidence"]],
            validated_evidence=[self._item_to_dict(item) for item in validation["validated_evidence"]],
            discarded_evidence=[self._item_to_dict(item) for item in validation["discarded_evidence"]],
            evidence_quality=validation["evidence_quality"],
            confidence_score=validation["confidence_score"],
            conflicts=conflicts["conflicts"],
            resolution_status=conflicts["resolution_status"],
            unresolved_conflicts_count=conflicts["unresolved_conflicts_count"],
            evidence_context=packaging["evidence_context"],
            facts_block=packaging["facts_block"],
            context_block=packaging["context_block"],
            conflict_block=packaging["conflict_block"],
            answerability=packaging["answerability"],
            evidence_states=collection["evidence_states"],
        )

    def _decompose_query(self, query: str) -> dict[str, Any]:
        normalized = " ".join((query or "").strip().split())
        lowered = normalized.lower()
        # Generate subquestions up to reasoning_steps; first step is always the query itself.
        subquestions = [normalized] if normalized else []
        if normalized and self.reasoning_steps > 1:
            step_templates = [
                "Was sind die moeglichen Ursachen fuer: {q}?",
                "Welche Quellen bestaetigen oder widerlegen: {q}?",
                "Welche Unsicherheiten bestehen bei der Beantwortung von: {q}?",
                "Welche Randbedingungen sind relevant fuer: {q}?",
            ]
            for tpl in step_templates[: self.reasoning_steps - 1]:
                subquestions.append(tpl.format(q=normalized))
        topic_tags: list[str] = []

        if FACTUAL_QUERY_RE.search(lowered):
            topic_tags.append("factual")
        if "diagnos" in lowered or "debug" in lowered:
            topic_tags.append("diagnostic")
        if "compare" in lowered or "vergleich" in lowered:
            topic_tags.append("comparison")
        if "source" in lowered or "quelle" in lowered:
            topic_tags.append("source_sensitive")

        time_scope = "recent" if TIME_SCOPE_RE.search(lowered) else "unspecified"

        design_query = bool(DESIGN_QUERY_RE.search(lowered))

        if CREATIVE_QUERY_RE.search(lowered) or CASUAL_QUERY_RE.search(lowered):
            required_level = "low"
        elif design_query and "source_sensitive" not in topic_tags and time_scope != "recent":
            # Technical architecture/design prompts should not hard-block on external evidence.
            required_level = "medium"
        elif "source_sensitive" in topic_tags or "factual" in topic_tags:
            required_level = "high"
        else:
            required_level = "medium"

        return {
            "normalized_query": normalized,
            "subquestions": subquestions,
            "topic_tags": topic_tags,
            "time_scope": time_scope,
            "required_evidence_level": required_level,
        }

    def _select_sources(
        self,
        *,
        query: str,
        required_level: str,
        source_counts: dict[str, int],
        tool_outputs: dict[str, Any],
        has_history: bool,
    ) -> dict[str, Any]:
        lowered = (query or "").lower()
        query_tokens = self._extract_terms(lowered)
        selected: list[str] = []
        skipped: list[str] = []
        reasons: list[str] = []

        if required_level == "low":
            selected = ["user_input_only"]
            reasons.append("creative_or_casual_query")
            if source_counts.get("system", 0) > 0:
                selected.insert(0, "system")
                reasons.append("canonical_system_context_available")
            for src in ["system", "facts", "memory", "rag", "web", "tool_output", "internal_status"]:
                if src not in selected:
                    skipped.append(src)
            return {
                "selected_sources": selected,
                "skipped_sources": skipped,
                "selection_reasoning": reasons,
            }

        if has_history:
            selected.append("memory")
            reasons.append("session_history_available")

        if source_counts.get("system", 0) > 0:
            selected.append("system")
            reasons.append("canonical_system_context_available")

        if source_counts.get("facts", 0) > 0 or any(k in query_tokens for k in {"fact", "fakt", "name", "age", "email"}):
            selected.append("facts")
            reasons.append("fact_relevant")

        if source_counts.get("qdrant", 0) > 0 or any(k in lowered for k in ["semantic", "ähnlich", "thema", "context"]):
            selected.append("rag")
            reasons.append("semantic_context_relevant")

        if source_counts.get("chroma", 0) > 0:
            selected.append("internal_status")
            reasons.append("working_context_available")

        if tool_outputs:
            selected.append("tool_output")
            reasons.append("tool_output_available")

        if required_level == "high" and any(k in lowered for k in ["aktuell", "latest", "current", "quelle", "source"]):
            selected.append("web")
            reasons.append("current_factual_query_prefers_web")

        if not selected:
            selected = ["user_input_only"]
            reasons.append("no_strong_source_match")

        all_sources = ["system", "facts", "memory", "rag", "web", "tool_output", "internal_status", "user_input_only"]
        skipped = [src for src in all_sources if src not in selected]

        return {
            "selected_sources": list(dict.fromkeys(selected)),
            "skipped_sources": skipped,
            "selection_reasoning": reasons,
        }

    # EvidenceState.value -> EvidenceItem.quality, per Issue #8's plan: a
    # weak/negative evidence state must never be treated as verified.
    _QUALITY_BY_EVIDENCE_STATE = {
        "found": "verified",
        "not_found_in_search": "insufficient",
        "unresolved": "insufficient",
        "connector_unavailable": "weak",
        "access_denied": "weak",
        "conflicting_evidence": "conflicting",
        "does_not_exist_confirmed": "verified",
        "private_confirmed": "verified",
    }

    @staticmethod
    def _resolve_evidence_target(output: Any, query: str) -> str:
        """Derive the target a single tool observation is actually about.

        Nephy round 2: classifying every observation against the full,
        possibly multi-entity request text (the raw `query` passed into
        analyze()) means two tool calls investigating two different things
        in the same turn (e.g. "check account A and repo B") end up with
        the *same* target string. merge_evidence_assertions() then treats
        them as observations of one thing and can silently collapse one
        entity's state into the other's, and the validator's multi-target
        guard never sees more than one distinct target to begin with.

        web_discovery/web_lookup (services/orchestrator/executor.py) already
        carry the actual per-call search phrase in `output["query"]` --
        that's a real, tool-call-specific identifier, not the whole request.
        Prefer it when present. There is currently no other per-call
        identifier (no resource_id/canonical_url/entity_id) anywhere in the
        real tool_outputs shapes, so anything else falls back to the overall
        query -- consistent with this issue's standing "no fictional
        connector, only what real tool outputs actually carry" scope.
        """
        if isinstance(output, dict):
            tool_query = str(output.get("query") or "").strip()
            if tool_query:
                return tool_query
        return query

    @staticmethod
    def _classify_tool_output_state(tool_name: str, output: Any, query: str) -> EvidenceAssertion:
        """Classify one tool output into an EvidenceAssertion.

        Never promotes a search-miss or a malformed/failed tool output into
        a confirmed-absence state -- only an explicit EvidenceConfirmation
        (which this classifier never fabricates) can produce
        DOES_NOT_EXIST_CONFIRMED/PRIVATE_CONFIRMED (see evidence_state.py).
        """
        target = EvidenceEngine._resolve_evidence_target(output, query)

        if not isinstance(output, dict):
            text = str(output or "").strip()
            if not text:
                return EvidenceAssertion.unresolved(target=target, source=tool_name, summary="Empty tool output.")
            return EvidenceAssertion.found(target=target, source=tool_name, summary=text[:200])

        status = str(output.get("status") or "").strip().lower()
        evidence_scope = str(output.get("evidence_scope") or "").strip().lower()
        error_text = str(output.get("error") or "")

        # Check access-denied BEFORE the generic failed/error/blocked branch:
        # a real 401/403 commonly surfaces as status="error" with the code
        # in the error text (e.g. {"status": "error", "error": "403
        # Forbidden"}), not status="denied". Classifying that as generic
        # CONNECTOR_UNAVAILABLE would let it bypass the ACCESS_DENIED-vs-
        # PRIVATE_CONFIRMED guard downstream (ACCESS_DENIED != PRIVATE).
        if status == "denied" or "401" in error_text or "403" in error_text:
            return EvidenceAssertion.access_denied(target=target, source=tool_name, summary=error_text[:200])
        if status in {"failed", "error", "blocked"}:
            return EvidenceAssertion.connector_unavailable(target=target, source=tool_name, summary=error_text[:200])

        if evidence_scope == "discovery":
            candidate_count = output.get("candidate_count")
            results = output.get("results")
            is_empty = (isinstance(candidate_count, int) and candidate_count == 0) or (
                isinstance(results, list) and len(results) == 0
            )
            if is_empty:
                return EvidenceAssertion.not_found_in_search(target=target, source=tool_name)
            return EvidenceAssertion.found(
                target=target, source=tool_name, summary=str(output.get("summary_text") or "")[:200], confidence=0.6
            )

        has_recognizable_shape = any(
            key in output for key in ("summary_text", "output", "content", "results", "items", "count")
        )
        if not has_recognizable_shape:
            return EvidenceAssertion.unresolved(target=target, source=tool_name)

        summary = str(output.get("summary_text") or output.get("output") or output.get("content") or "")[:200]
        return EvidenceAssertion.found(target=target, source=tool_name, summary=summary, confidence=0.8)

    def _collect_evidence(
        self,
        *,
        context_channels: dict[str, str],
        tool_outputs: dict[str, Any],
        selected_sources: list[str],
        query: str,
    ) -> dict[str, Any]:
        items: list[EvidenceItem] = []
        fetch_outcomes: dict[str, str] = {}
        evidence_states: list[dict[str, Any]] = []

        def add_lines(source: str, text: str, max_items: int | None = None) -> None:
            if max_items is None:
                max_items = self.max_items_per_source
            lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
            seen: set[str] = set()
            for ln in lines:
                if ln in seen:
                    continue
                seen.add(ln)
                items.append(EvidenceItem(source=source, summary=ln[:280], quality="plausible", relevance=0.7))
                if len(seen) >= max_items:
                    break
            fetch_outcomes[source] = "ok" if lines else "empty"

        if "system" in selected_sources:
            add_lines("system", context_channels.get("system_context", ""))

        if "facts" in selected_sources:
            add_lines("facts", context_channels.get("fact_context", ""))
        if "memory" in selected_sources:
            add_lines("memory", context_channels.get("memory_context", ""))
        if "rag" in selected_sources:
            add_lines("rag", context_channels.get("working_context", ""))
        if "internal_status" in selected_sources:
            add_lines("internal_status", context_channels.get("relation_context", ""))

        if "tool_output" in selected_sources:
            if tool_outputs:
                tool_assertions: list[EvidenceAssertion] = []
                for tool_name, output in sorted(tool_outputs.items()):
                    text = ""
                    if isinstance(output, dict):
                        text = str(output.get("summary_text") or output.get("output") or output)
                    else:
                        text = str(output)
                    text = " ".join(text.split())

                    assertion = self._classify_tool_output_state(tool_name, output, query)
                    tool_assertions.append(assertion)
                    item_quality = self._QUALITY_BY_EVIDENCE_STATE.get(assertion.state.value, "verified")

                    if text:
                        items.append(
                            EvidenceItem(
                                source="tool_output",
                                summary=f"[{tool_name}] {text[:260]}",
                                quality=item_quality,
                                relevance=0.8,
                                freshness=0.8,
                                metadata={"tool": tool_name},
                                evidence_state=assertion.state.value,
                            )
                        )
                fetch_outcomes["tool_output"] = "ok"
                evidence_states = [a.to_dict() for a in merge_evidence_assertions(tool_assertions)]
            else:
                fetch_outcomes["tool_output"] = "empty"

        if "web" in selected_sources:
            # Web retrieval is not performed here; mark as skipped/empty.
            fetch_outcomes["web"] = "skipped"

        if "user_input_only" in selected_sources:
            items.append(EvidenceItem(source="user_input_only", summary=query[:260], quality="weak", relevance=0.4))
            fetch_outcomes["user_input_only"] = "ok"

        for src in selected_sources:
            fetch_outcomes.setdefault(src, "empty")

        non_empty = [status for status in fetch_outcomes.values() if status == "ok"]
        if non_empty and len(non_empty) == len(fetch_outcomes):
            retrieval_status = "ok"
        elif non_empty:
            retrieval_status = "partial"
        elif any(v == "skipped" for v in fetch_outcomes.values()):
            retrieval_status = "skipped"
        else:
            retrieval_status = "empty"

        return {
            "evidence_items": items,
            "fetch_outcomes": fetch_outcomes,
            "retrieval_status": retrieval_status,
            "evidence_states": evidence_states,
        }

    def _semantic_filter_evidence(
        self,
        *,
        evidence_items: list[EvidenceItem],
        normalized_query: str,
    ) -> dict[str, Any]:
        if not self.semantic_filter_enabled:
            return {
                "filtered_evidence": evidence_items,
                "discarded_evidence": [],
            }

        query_terms = self._extract_terms(normalized_query)
        if not query_terms:
            return {
                "filtered_evidence": evidence_items,
                "discarded_evidence": [],
            }

        filtered: list[EvidenceItem] = []
        discarded: list[EvidenceItem] = []
        for item in evidence_items:
            summary_terms = self._extract_terms(item.summary)
            overlap = (len(query_terms & summary_terms) / len(query_terms)) if summary_terms else 0.0
            semantic_relevance = round(max(0.0, min(1.0, overlap)), 3)
            item.metadata["semantic_relevance"] = semantic_relevance
            item.metadata["semantic_min_relevance"] = self.semantic_min_relevance

            # Keep verified evidence slightly more permissive to avoid losing hard facts.
            effective_threshold = self.semantic_min_relevance
            if item.source in {"system", "facts", "tool_output"}:
                effective_threshold = max(0.0, self.semantic_min_relevance * 0.6)

            if semantic_relevance >= effective_threshold:
                item.relevance = round(max(item.relevance, semantic_relevance), 3)
                filtered.append(item)
            else:
                item.quality = "weak"
                discarded.append(item)

        if not filtered:
            # Avoid hard-empty downstream; pass at least the strongest candidate.
            if evidence_items:
                strongest = max(
                    evidence_items,
                    key=lambda it: float((it.metadata or {}).get("semantic_relevance", 0.0)),
                )
                filtered.append(strongest)
                discarded = [it for it in evidence_items if it is not strongest]

        return {
            "filtered_evidence": filtered,
            "discarded_evidence": discarded,
        }

    @staticmethod
    def _extract_terms(text: str) -> set[str]:
        normalized = (text or "").strip().lower()
        tokens = re.findall(r"[A-Za-z0-9_\-]+", normalized)

        # Resolve stopwords dynamically from detected language, without hardcoded word lists.
        stop_words: set[str] = set()
        try:
            from langdetect import detect as _langdetect
            from stop_words import get_stop_words as _get_stop_words

            lang = _langdetect(normalized[:200]) if len(normalized) >= 10 else "en"
            stop_words = set(_get_stop_words(lang or "en"))
        except Exception:
            stop_words = set()

        return {tok for tok in tokens if len(tok) >= 3 and tok not in stop_words}

    def _validate_evidence(self, evidence_items: list[EvidenceItem], required_level: str) -> dict[str, Any]:
        validated: list[EvidenceItem] = []
        discarded: list[EvidenceItem] = []

        for item in evidence_items:
            summary = item.summary.lower()
            if len(item.summary.strip()) < 8:
                item.quality = "weak"
                discarded.append(item)
                continue
            if item.source in {"tool_output", "facts"}:
                # A classified evidence_state (Issue #8) takes precedence
                # over the blanket "tool_output source == verified" rule --
                # a NOT_FOUND_IN_SEARCH/UNRESOLVED/CONNECTOR_UNAVAILABLE
                # tool output must never get promoted to "verified" quality
                # just because it came from a tool.
                if item.evidence_state and item.evidence_state in self._QUALITY_BY_EVIDENCE_STATE:
                    item.quality = self._QUALITY_BY_EVIDENCE_STATE[item.evidence_state]
                else:
                    item.quality = "verified"
                item.relevance = max(item.relevance, 0.8)
            elif item.source in {"memory", "rag", "internal_status"}:
                item.quality = "plausible"
                item.relevance = max(item.relevance, 0.65)
            elif item.source == "user_input_only":
                item.quality = "weak"
            if "error" in summary or "failed" in summary:
                item.quality = "weak"
            validated.append(item)

        if not validated:
            evidence_quality = "insufficient"
            confidence = 0.0
        else:
            verified_count = sum(1 for i in validated if i.quality == "verified")
            plausible_count = sum(1 for i in validated if i.quality == "plausible")
            weak_count = sum(1 for i in validated if i.quality == "weak")
            total = len(validated)
            confidence = round((verified_count * 1.0 + plausible_count * 0.65 + weak_count * 0.25) / total, 3)
            if required_level == "high" and verified_count == 0 and plausible_count < 2:
                evidence_quality = "weak"
            elif confidence >= self.confidence_strong_threshold:
                evidence_quality = "strong"
            elif confidence >= self.confidence_medium_threshold:
                evidence_quality = "medium"
            else:
                evidence_quality = "weak"

        return {
            "validated_evidence": validated,
            "discarded_evidence": discarded,
            "evidence_quality": evidence_quality,
            "confidence_score": confidence,
        }

    def _resolve_conflicts(self, validated_evidence: list[EvidenceItem]) -> dict[str, Any]:
        key_to_values: dict[str, set[str]] = {}

        fact_line_re = re.compile(r"\[fact:[^\]]+\]\s*([A-Za-z0-9_\-]+)\s*:\s*(.+)$")
        for item in validated_evidence:
            match = fact_line_re.search(item.summary)
            if not match:
                continue
            key = match.group(1).strip().lower()
            value = match.group(2).strip().lower()
            key_to_values.setdefault(key, set()).add(value)

        conflicts: list[dict[str, Any]] = []
        for key, values in sorted(key_to_values.items()):
            if len(values) > 1:
                conflicts.append(
                    {
                        "key": key,
                        "values": sorted(values),
                        "status": "unresolved",
                        "reason": "multiple_distinct_fact_values",
                    }
                )

        unresolved = len(conflicts)
        resolution_status = "none" if unresolved == 0 else "unresolved"

        return {
            "conflicts": conflicts,
            "resolution_status": resolution_status,
            "unresolved_conflicts_count": unresolved,
        }

    def _derive_answerability(
        self,
        *,
        retrieval_status: str,
        evidence_quality: str,
        unresolved_conflicts: int,
        required_level: str,
    ) -> str:
        if unresolved_conflicts > 0:
            return "limited"
        if retrieval_status in {"empty", "failed"}:
            return "insufficient"
        if required_level == "high" and evidence_quality in {"weak", "insufficient"}:
            return "insufficient"
        if evidence_quality == "insufficient":
            return "blocked"
        return "sufficient"

    def _package_context(
        self,
        *,
        normalized_query: str,
        selected_sources: list[str],
        validated_evidence: list[EvidenceItem],
        conflicts: list[dict[str, Any]],
        confidence_score: float,
        answerability: str,
    ) -> dict[str, Any]:
        facts_block: list[str] = []
        context_block: list[str] = []
        for item in validated_evidence:
            line = f"{item.summary}"
            if item.source in {"facts", "tool_output"}:
                facts_block.append(line)
            else:
                context_block.append(line)

        conflict_block = [f"{c['key']}: {' | '.join(c['values'])}" for c in conflicts]

        facts_block = facts_block[:8]
        context_block = context_block[:8]
        conflict_block = conflict_block[:6]

        evidence_context = (
            "[EVIDENCE_CONTEXT]\n"
            f"query={normalized_query}\n"
            f"selected_sources={','.join(selected_sources) or '(none)'}\n"
            f"confidence={confidence_score}\n"
            f"answerability={answerability}\n"
            "[FACTS_BLOCK]\n"
            + ("\n".join(f"- {line}" for line in facts_block) if facts_block else "- (none)")
            + "\n[CONTEXT_BLOCK]\n"
            + ("\n".join(f"- {line}" for line in context_block) if context_block else "- (none)")
            + "\n[CONFLICT_BLOCK]\n"
            + ("\n".join(f"- {line}" for line in conflict_block) if conflict_block else "- (none)")
        )

        return {
            "evidence_context": evidence_context,
            "facts_block": facts_block,
            "context_block": context_block,
            "conflict_block": conflict_block,
            "answerability": answerability,
        }

    @staticmethod
    def _item_to_dict(item: EvidenceItem) -> dict[str, Any]:
        return {
            "source": item.source,
            "summary": item.summary,
            "quality": item.quality,
            "relevance": item.relevance,
            "freshness": item.freshness,
            "consistent": item.consistent,
            "metadata": dict(item.metadata or {}),
            "evidence_state": item.evidence_state,
        }
