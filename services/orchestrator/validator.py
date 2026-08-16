"""Output validator for ensuring response quality.

Implements staged checks and emits an explicit decision signal:
accept, revise, warn, or block.
"""

import json
import re
from typing import Any, Dict

from services.contracts import ValidationContext, ValidationResult


class ResponseValidator:
    """Validates LLM responses against quality criteria."""

    # Tools that provide externally grounded facts and therefore require attribution.
    _ATTRIBUTION_REQUIRED_TOOLS = frozenset({"web_search"})
    _YEAR_QUERY_RE = re.compile(r"\b(jahr|year|wann|datum|date)\b", re.IGNORECASE)
    _TOOL_DIRECTED_FACT_QUERY_RE = re.compile(
        r"^(?:suche|such|search|finde|find|prüfe|pruefe|check|recherchiere|lookup|schau(?:e)?\s+nach)\b",
        re.IGNORECASE,
    )
    _YEAR_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2}|21\d{2})\b")
    _GRAPH_RELATION_RE = re.compile(
        r"\[relation\]\s*(?P<source>.+?)\s*-\[(?P<relation>[^\]]+)\]->\s*(?P<target>.+)",
        re.IGNORECASE,
    )

    def __init__(self, strict_mode: bool = True):
        self.strict_mode = strict_mode
        self.validators = {
            "fast_check": self._check_fast_check,
            "tool_evidence_integrity": self._check_tool_evidence_integrity,
            "evidence_state_integrity": self._check_evidence_state_integrity,
            "vision_evidence_integrity": self._check_vision_evidence_integrity,
            "source_attribution": self._check_source_attribution,
            "consistency": self._check_consistency,
            "graph_priority": self._check_graph_priority,
            "grounding": self._check_grounding,
            "safety": self._check_safety,
            "command_response_mismatch": self._check_command_response_mismatch,
        }

    def validate(self, context: ValidationContext) -> ValidationResult:
        """Run all validators and aggregate results."""
        issues = []
        checks: Dict[str, str] = {}
        quality_boost = self._compute_quality_boost(context)
        confidence_score = min(1.0, round(0.88 + quality_boost, 4))

        extra_risk_flags: list[str] = []
        for check_name, validator_fn in self.validators.items():
            result = validator_fn(context)
            checks[check_name] = "pass" if result.get("passed", False) else "fail"
            if not result["passed"]:
                issues.extend(result.get("issues", []))
                confidence_score *= result.get("confidence_penalty", 0.9)
                extra_risk_flags.extend(result.get("risk_flags", []))

        feedback_score = self._resolve_optional_feedback_score(context)
        if feedback_score is not None:
            confidence_score = self._blend_system_and_user_confidence(
                system_confidence=confidence_score,
                user_feedback_score=feedback_score,
                system_weight=0.85,
                user_weight=0.15,
            )

        confidence_score = round(min(1.0, max(0.0, confidence_score)), 2)

        risk_flags, score_issues = self._collect_risk_flags_and_issues(
            context=context,
            checks=checks,
            confidence_score=confidence_score,
        )
        # Merge in any extra reason codes individual checks emitted
        # directly (Issue #8): _collect_risk_flags_and_issues derives one
        # fixed flag per failed check name, but a single check can surface
        # several distinct reason codes (e.g. evidence_state_integrity).
        risk_flags = sorted(set(risk_flags) | set(extra_risk_flags))
        for issue in score_issues:
            if issue not in issues:
                issues.append(issue)

        decision = self._decide(issues, confidence_score)
        decision = self._apply_source_risk_reassessment(
            context=context,
            decision=decision,
            risk_flags=risk_flags,
            issues=issues,
        )
        passed = decision == "accept" if self.strict_mode else decision in {"accept", "warn"}

        suggestions = None
        if decision == "revise":
            suggestions = [
                "Improve source grounding and clarify unsupported claims.",
                "Retry generation with tool-backed evidence emphasis.",
            ]
        elif decision == "warn":
            suggestions = [
                "Output can be shown with uncertainty note.",
            ]
        elif decision == "block":
            suggestions = [
                "Do not present this answer to the user.",
                "Regenerate with stronger grounding or safer response framing.",
            ]

        return ValidationResult(
            passed=passed,
            decision=decision,
            checks=checks,
            issues=issues,
            confidence_score=confidence_score,
            suggestions=suggestions,
            score=None,
            risk_flags=risk_flags,
        )

    @staticmethod
    def _apply_source_risk_reassessment(
        *,
        context: ValidationContext,
        decision: str,
        risk_flags: list[str],
        issues: list[str],
    ) -> str:
        """Reassess risk for trusted bridge-origin traffic without bypassing safety blocks."""
        if not bool(getattr(context, "risk_reassessment", False)):
            return decision
        if (str(getattr(context, "request_source", "") or "").strip().lower() != "openai_bridge_service"):
            return decision

        # Never relax explicit policy/safety problems.
        hard_blockers = {
            "policy_safety_violation",
            "formula_mismatch",
            "unit_mismatch",
            "logic_branch_dead",
            "crash_without_try_except",
            # Issue #8: as serious as a policy/safety violation -- these mean
            # the response asserted something the evidence doesn't support.
            "negative_existence_without_evidence",
            "connector_unknown_collapsed_to_false",
        }
        if any(flag in hard_blockers for flag in (risk_flags or [])):
            return decision

        # For bridge-origin requests, downgrade confidence-only blocks to revise.
        confidence_only_flags = {
            "grounding_weak",
            "attribution_missing",
            "consistency_issue",
            "format_or_viability_issue",
            "confidence_low",
        }
        non_confidence_flags = [f for f in (risk_flags or []) if f not in confidence_only_flags]
        if decision == "block" and not non_confidence_flags:
            if "Risk reassessed for openai_bridge_service: downgraded block to revise." not in issues:
                issues.append("Risk reassessed for openai_bridge_service: downgraded block to revise.")
            return "revise"

        if decision == "revise" and (risk_flags or []) == ["confidence_low"]:
            if "Risk reassessed for openai_bridge_service: downgraded revise to warn." not in issues:
                issues.append("Risk reassessed for openai_bridge_service: downgraded revise to warn.")
            return "warn"

        return decision

    @staticmethod
    def _compute_quality_boost(context: "ValidationContext") -> float:
        """Additive quality bonus in [0.0, 0.15] based on response signals.

        Positive signals push the baseline above 0.88 toward 1.0.
        No signal = neutral 0.88 (not perfect, just nothing wrong).
        """
        boost = 0.0
        stripped = (context.response or "").strip()
        length = len(stripped)

        # Optimal response length: 150-3000 chars
        if 150 <= length <= 3000:
            boost += 0.04
        elif 50 <= length < 150:
            boost += 0.02

        # Tool evidence present (routing chose tools and got results)
        if ResponseValidator._successful_tool_outputs(context.tool_outputs):
            boost += 0.05

        # Structured content: bullets, headers, code blocks
        if (
            stripped.count("\n") >= 3
            or "- " in stripped
            or "* " in stripped
            or "```" in stripped
            or "# " in stripped
        ):
            boost += 0.03

        # Active memory sources grounded the response
        source_total = sum(
            int(v or 0)
            for v in (context.context_sources or {}).values()
            if isinstance(v, (int, float))
        )
        if source_total > 0:
            boost += 0.03

        return round(boost, 4)

    @staticmethod
    def _normalize_optional_feedback_score(raw_score: Any) -> float | None:
        """Normalize optional user score into [0,1], or return None if absent/invalid."""
        if raw_score is None:
            return None
        try:
            score = float(raw_score)
        except (TypeError, ValueError):
            return None
        if score < 0.0:
            return 0.0
        if score > 1.0:
            return 1.0
        return score

    @classmethod
    def _resolve_optional_feedback_score(cls, context: "ValidationContext") -> float | None:
        """Resolve feedback score with precedence: explicit score, then normalized stars."""
        normalized_score = cls._normalize_optional_feedback_score(context.user_feedback_score)
        if normalized_score is not None:
            return normalized_score

        if context.user_feedback_stars is None:
            return None

        try:
            stars = int(context.user_feedback_stars)
        except (TypeError, ValueError):
            return None

        # 1 star -> 0.0, 6 stars -> 1.0
        stars = max(1, min(6, stars))
        return round((stars - 1) / 5.0, 4)

    @staticmethod
    def _blend_system_and_user_confidence(
        *,
        system_confidence: float,
        user_feedback_score: float,
        system_weight: float,
        user_weight: float,
    ) -> float:
        """Blend system + human confidence with system as dominant signal."""
        sw = max(0.0, float(system_weight))
        uw = max(0.0, float(user_weight))
        total = sw + uw
        if total <= 0.0:
            return max(0.0, min(1.0, float(system_confidence)))
        normalized_sw = sw / total
        normalized_uw = uw / total
        blended = (normalized_sw * float(system_confidence)) + (normalized_uw * float(user_feedback_score))
        return max(0.0, min(1.0, blended))

    @staticmethod
    def _contains_kw_ps_task(query: str) -> bool:
        q = (query or "").lower()
        return "kw" in q and "ps" in q and "nach" in q

    def _collect_risk_flags_and_issues(
        self,
        *,
        context: ValidationContext,
        checks: Dict[str, str],
        confidence_score: float,
    ) -> tuple[list[str], list[str]]:
        """Collect risk flags and deterministic issues without school-grade scoring."""
        risk_flags: list[str] = []
        issues: list[str] = []

        if checks.get("grounding") == "fail":
            risk_flags.append("grounding_weak")
        if checks.get("source_attribution") == "fail":
            risk_flags.append("attribution_missing")
        if checks.get("consistency") == "fail":
            risk_flags.append("consistency_issue")
        if checks.get("fast_check") == "fail":
            risk_flags.append("format_or_viability_issue")
        if checks.get("command_response_mismatch") == "fail":
            risk_flags.append("command_response_mismatch")
        if checks.get("tool_evidence_integrity") == "fail":
            risk_flags.append("tool_evidence_integrity")
        if checks.get("vision_evidence_integrity") == "fail":
            risk_flags.append("vision_evidence_integrity")
        if checks.get("safety") == "fail":
            risk_flags.append("policy_safety_violation")

        query_l = (context.original_query or "").lower()
        response_l = (context.response or "").lower()

        # Hard error: wrong formula for kW -> PS in conversion tasks.
        if self._contains_kw_ps_task(query_l) and "kw nach ps" in query_l:
            if "0.7457" in response_l or "0.745699" in response_l:
                risk_flags.append("formula_mismatch")
                issues.append("Falsche Formel fuer kW->PS erkannt.")

        # Hard error: unit mismatch (Watt instead of kW) for kW/PS conversion tasks.
        if self._contains_kw_ps_task(query_l) and "watt" in response_l:
            risk_flags.append("unit_mismatch")
            issues.append("Einheitenkonflikt erkannt (Watt statt kW/PS-Kontext).")

        # Hard error: likely dead branch caused by duplicated if/elif condition.
        if "elif" in response_l and "if" in response_l and "umrechnungsrichtung" in response_l:
            same_branch_pattern = re.search(
                r"if\s+umrechnungsrichtung\s+in\s*\[(.*?)\].*?elif\s+umrechnungsrichtung\s+in\s*\[(.*?)\]",
                response_l,
                flags=re.DOTALL,
            )
            if same_branch_pattern and same_branch_pattern.group(1).strip() == same_branch_pattern.group(2).strip():
                risk_flags.append("logic_branch_dead")
                issues.append("Potenziell toter Code-Zweig erkannt (duplizierte if/elif-Bedingung).")

        # Hard error: interactive numeric input without try/except guard.

        # Hard error: interactive numeric input without try/except guard.
        if "input(" in response_l and "float(" in response_l and ("try:" not in response_l or "except" not in response_l):
            risk_flags.append("crash_without_try_except")
            issues.append("Eingabe kann bei ValueError abstuerzen.")
        elif "input(" in response_l and "float(" in response_l:
            risk_flags.append("numeric_input_without_guard")

        if confidence_score < 0.75:
            risk_flags.append("confidence_low")

        return sorted(set(risk_flags)), issues

    @staticmethod
    def _decide(issues: list[str], confidence_score: float) -> str:
        """Map validation findings to one of four validator decisions."""
        issue_text = " ".join(issues).lower()

        if any(keyword in issue_text for keyword in {
            "policy",
            "unsafe",
            "forbidden",
            "security",
            "command mismatch",
            "fabricated tool execution",
        }):
            return "block"
        if "severe contradiction" in issue_text:
            return "block"
        if "factual answer appears ungrounded" in issue_text:
            return "revise"
        if any(keyword in issue_text for keyword in {"too short", "too long", "empty", "invalid json", "malformed markdown"}):
            return "revise"
        if confidence_score < 0.45:
            return "block"
        if confidence_score < 0.75:
            return "warn"
        if issues:
            return "revise"
        return "accept"

    @staticmethod
    def _has_knowledge_reference_marker(response: str) -> bool:
        return "[knowledge_reference]" in (response or "").lower()

    @staticmethod
    def _is_successful_tool_output(value: Any) -> bool:
        """Return true only for outputs that may ground factual claims."""
        if value is None:
            return False
        if not isinstance(value, dict):
            return True
        if value.get("evidence") is False:
            return False
        if str(value.get("evidence_scope") or "").strip().lower() == "discovery":
            return False
        status = str(value.get("status") or "").strip().lower()
        kind = str(value.get("kind") or "").strip().lower()
        if status in {"failed", "error", "blocked", "denied"}:
            return False
        if kind in {"tool_execution_failure", "judge_blocked", "judge_revise"}:
            return False
        if "_judge_blocked" in value:
            return False
        return True

    @classmethod
    def _successful_tool_outputs(cls, outputs: Dict[str, Any] | None) -> Dict[str, Any]:
        return {
            name: value
            for name, value in (outputs or {}).items()
            if not str(name).startswith("_judge_") and cls._is_successful_tool_output(value)
        }

    @classmethod
    def _check_tool_evidence_integrity(cls, context: ValidationContext) -> Dict[str, Any]:
        """Reject claims that a tool ran when no successful result exists."""
        response = context.response or ""
        response_l = response.lower()
        successful_outputs = cls._successful_tool_outputs(context.tool_outputs)
        has_successful_evidence = bool(successful_outputs)

        internal_result_marker = bool(
            re.search(
                r"[\[【]\s*(?:sys|tool(?:_result)?|knowledge_reference|knoweldge_reference)\s*(?::[^\]】]*)?[\]】]",
                response,
                flags=re.IGNORECASE,
            )
        )
        execution_claim = bool(
            re.search(
                r"\b(?:ausgef(?:ü|ue)hrt(?:er|e|en)?\s+befehl|"
                r"(?:wurde|wurden|habe|haben)\b.{0,80}\b(?:ausgef(?:ü|ue)hrt|abgerufen|gestellt|gesendet|angefragt|aufgerufen)|"
                r"abfragen?\b.{0,80}\b(?:gesendet|gestellt|ausgef(?:ü|ue)hrt)|"
                r"executed|ran\s+(?:curl|command)|tool\s+(?:was\s+)?executed)\b",
                response_l,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        evidence_claim = bool(
            re.search(
                r"\b(?:api|tool|curl|suche|search|abfrage|request|befehl|command)\b"
                r".{0,45}\b(?:liefert|lieferte|ergab|ergibt|antwortete|zeigt|zeigte|"
                r"returned|returns|found|result|response)\b",
                response_l,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )
        explicitly_denied = bool(
            re.search(
                r"\b(?:nicht|kein|keine|never|not|failed|fehlgeschlagen)\b.{0,45}"
                r"\b(?:ausgef(?:ü|ue)hrt|tool|curl|command|befehl|abfrage)\b",
                response_l,
                flags=re.IGNORECASE | re.DOTALL,
            )
        )

        issues: list[str] = []
        if not has_successful_evidence and (
            internal_result_marker or ((execution_claim or evidence_claim) and not explicitly_denied)
        ):
            issues.append("Fabricated tool execution claim: no successful tool result is available")

        if execution_claim:
            claimed_urls = {
                match.rstrip(".,;:!?)]")
                for match in re.findall(r"https?://[^\s<>\"']+", response, flags=re.IGNORECASE)
            }
            fetched_urls = {
                str(value.get("url") or "").strip()
                for value in successful_outputs.values()
                if isinstance(value, dict) and str(value.get("kind") or "") == "url_fetch"
            }
            fetched_urls.discard("")
            unexecuted_urls = claimed_urls - fetched_urls
            if unexecuted_urls:
                issues.append(
                    "Fabricated URL execution claim: response names URL(s) absent from successful fetch evidence"
                )

        return {
            "passed": not issues,
            "issues": issues,
            "confidence_penalty": 0.2 if issues else 1.0,
        }

    # Issue #8: negative-existence / privacy / connector-as-absence /
    # hypothesis-promotion pattern classes for _check_evidence_state_integrity.
    _NEGATIVE_EXISTENCE_RE = re.compile(
        r"\b(?:does not exist|doesn't exist|kein(?:e)?\s+(?:konto|account|user|profil)\s+(?:existiert|vorhanden)|"
        r"not\s+(?:a\s+)?(?:real|valid|existing)|nicht\s+vorhanden|existiert\s+nicht|no\s+such\s+(?:user|account|repo))\b",
        re.IGNORECASE,
    )
    _PRIVACY_CLAIM_RE = re.compile(
        r"\b(?:is\s+private|ist\s+privat|has\s+been\s+made\s+private|privat\s+gestellt)\b",
        re.IGNORECASE,
    )
    _UNAVAILABLE_AS_ABSENT_RE = re.compile(
        r"\b(?:the\s+resource\s+is\s+(?:unavailable|absent|gone)|no\s+longer\s+exists|"
        r"nicht\s+(?:mehr\s+)?verf(?:ü|ue)gbar\s+und\s+existiert\s+nicht)\b",
        re.IGNORECASE,
    )
    _HYPOTHESIS_RE = re.compile(
        r"\b(?:may\s+(?:be|not)|might\s+(?:be|not)|possibly|vermutlich|k(?:ö|oe)nnte|"
        r"m(?:ö|oe)glicherweise|not\s+indexed|nicht\s+indexiert|could\s+not\s+determine)\b",
        re.IGNORECASE,
    )
    _DEPENDENCY_MARKER_RE = re.compile(r"\b(?:therefore|thus|daher|folglich|deshalb|as a result|consequently)\b", re.IGNORECASE)

    @classmethod
    def _check_evidence_state_integrity(cls, context: ValidationContext) -> Dict[str, Any]:
        """Reject responses that promote a weak/negative evidence state into
        a stronger factual claim without a supporting evidence edge (Issue #8).

        EvidenceState (services/contracts/evidence_state.py) is the
        normative layer -- the actual source-of-truth classification of what
        is known/unknown about a target. This check's regex analysis of the
        final natural-language response is a conservative *enforcement
        heuristic* on top of that normative layer, not a semantic claim
        parser: it will have false negatives on phrasings it doesn't
        recognize, and that is an accepted v1 limitation, not a bug to
        silently "fix" later by treating this regex layer as if it were a
        ground-truth claim extractor.
        """
        response = context.response or ""
        assertions = [
            item for item in (getattr(context, "evidence_states", None) or []) if isinstance(item, dict)
        ]
        states = {str(item.get("state") or "") for item in assertions}

        issues: list[str] = []
        risk_flags: list[str] = []

        # Nephy round 2: a *_CONFIRMED state for target A must never
        # blanket-authorize a claim about target B just because both
        # appear somewhere in the same evidence_states list. When more than
        # one distinct target is present, a claim is only considered
        # supported if the confirming assertion's target identifier
        # actually appears near that specific claim in the response text.
        # With zero or one target there is no such ambiguity to guard
        # against, so the cheaper "is the state present at all" check is
        # kept (also avoids false positives when the LLM doesn't literally
        # restate the target's exact identifier next to the claim).
        #
        # Issue #12: assertions carrying a canonical_target get a strictly
        # stronger, two-step check instead of the free-text fallback below --
        # target resolution and state authorization are kept as separate
        # steps on purpose (Nephy plan-review correction). Restricting the
        # identifier search to assertions that already carry the required
        # state would silently exclude the claim's real target from
        # consideration and let an unrelated confirmed target's identifier
        # win by default -- reopening the exact cross-target leak this
        # method exists to close, one layer deeper. So step 1 below (which
        # canonical target does this claim refer to?) is evaluated across
        # every canonical-bearing assertion regardless of state; only once
        # a claim is bound to exactly one target does step 2 ask whether
        # that specific target carries the required state -- with no
        # fallback to a different target's state, and no fallback to the
        # legacy text-target check either.
        text_only_assertions = [item for item in assertions if item.get("canonical_target") is None]
        text_only_distinct_targets = {
            str(item.get("target") or "") for item in text_only_assertions if item.get("target")
        }
        text_only_multi_target = len(text_only_distinct_targets) > 1

        def _canonical_identifiers(canonical: Dict[str, Any]) -> list[str]:
            # display_name is deliberately excluded (user/Nephy decision,
            # Issue #12 round 1): it's presentation-only per the contract.
            # Only canonical_ref and explicitly declared aliases carry
            # claim-binding authority -- mirrors EvidenceTarget.identifiers().
            values = [str(canonical.get("canonical_ref") or "")]
            aliases = canonical.get("aliases")
            if isinstance(aliases, (list, tuple, set, frozenset)):
                values.extend(str(alias) for alias in aliases)
            return [value.lower() for value in values if value]

        def _claim_supported_by_target(match: "re.Match[str]", required_state: str, window: int = 100) -> bool:
            s_start, s_end = match.span()
            lo, hi = max(0, s_start - window), min(len(response), s_end + window)
            nearby = response[lo:hi].lower()

            # Step 1: which canonical target (if any) does this claim refer
            # to? Whole-string (never fragment/token) identifier matching,
            # independent of `required_state` -- see the module note above.
            canonical_matches: dict[tuple[str, str], Dict[str, Any]] = {}
            for item in assertions:
                canonical = item.get("canonical_target")
                if not isinstance(canonical, dict):
                    continue
                namespace = str(canonical.get("namespace") or "")
                canonical_ref = str(canonical.get("canonical_ref") or "")
                if not namespace or not canonical_ref:
                    continue
                key = (namespace, canonical_ref)
                if key in canonical_matches:
                    continue
                if any(identifier in nearby for identifier in _canonical_identifiers(canonical)):
                    canonical_matches[key] = canonical

            if len(canonical_matches) > 1:
                return False  # ambiguous overlap -- fail closed, no fallback

            if len(canonical_matches) == 1:
                (bound_key,) = canonical_matches.keys()
                # Step 2: does THAT specific target carry the required
                # state? No fallback to a different target's state, and no
                # fallback to the text heuristic below, even if this target
                # simply lacks the required state.
                for item in assertions:
                    canonical = item.get("canonical_target")
                    if not isinstance(canonical, dict):
                        continue
                    key = (str(canonical.get("namespace") or ""), str(canonical.get("canonical_ref") or ""))
                    if key == bound_key and str(item.get("state") or "") == required_state:
                        return True
                return False

            # Step 3: no canonical target was identified at all -- fall back
            # to the pre-#12 tokenized text-target check, restricted to
            # unresolved/text-only assertions so a canonical-bearing
            # assertion can't "re-enter" the check through its legacy free-
            # text `target` string (step 1 already determined this claim
            # isn't about any known canonical target).
            if not text_only_multi_target:
                return required_state in {str(item.get("state") or "") for item in text_only_assertions}
            for item in text_only_assertions:
                if str(item.get("state") or "") != required_state:
                    continue
                target = str(item.get("target") or "").strip().lower()
                tokens = [tok for tok in re.findall(r"[a-z0-9]+", target) if len(tok) >= 3]
                if not tokens or any(tok in nearby for tok in tokens):
                    return True
            return False

        for match in cls._NEGATIVE_EXISTENCE_RE.finditer(response):
            if not _claim_supported_by_target(match, "does_not_exist_confirmed"):
                issues.append(
                    "Negative existence claim not backed by an explicit does-not-exist confirmation "
                    "for this target (evidence state is search-miss/unresolved/for a different target, "
                    "not confirmed absence of this one)"
                )
                risk_flags.append("negative_existence_without_evidence")
                break

        for match in cls._PRIVACY_CLAIM_RE.finditer(response):
            if "access_denied" not in states:
                continue
            if not _claim_supported_by_target(match, "private_confirmed"):
                issues.append(
                    "Privacy claim derived from an access-denied response without explicit source "
                    "confirmation for this target"
                )
                risk_flags.append("unsupported_state_promotion")
                break

        if cls._UNAVAILABLE_AS_ABSENT_RE.search(response) and ({"connector_unavailable", "unresolved"} & states):
            issues.append("Connector/tool failure presented as a negative world-state observation")
            risk_flags.append("connector_unknown_collapsed_to_false")

        strong_spans = list(cls._NEGATIVE_EXISTENCE_RE.finditer(response)) + list(cls._PRIVACY_CLAIM_RE.finditer(response))
        if cls._HYPOTHESIS_RE.search(response) and strong_spans:
            hedge_spans = [m.span() for m in cls._HYPOTHESIS_RE.finditer(response)]
            dependency_positions = [m.start() for m in cls._DEPENDENCY_MARKER_RE.finditer(response)]

            def _hedge_covers_claim(h_start: int, h_end: int, s_start: int, s_end: int) -> bool:
                close = abs(s_start - h_end) < 60 or abs(h_start - s_end) < 60
                if not close:
                    return False
                # A dependency marker ("therefore"/"thus"/...) sitting
                # between the hedge and the claim signals a NEW, separately
                # asserted claim, not the same hedged one restated -- so it
                # does not count as "covered" even if the two spans are
                # textually close.
                lo, hi = (h_end, s_start) if h_end <= s_start else (s_end, h_start)
                return not any(lo <= pos <= hi for pos in dependency_positions)

            for match in strong_spans:
                s_start, s_end = match.span()
                near_hedge = any(_hedge_covers_claim(h_start, h_end, s_start, s_end) for h_start, h_end in hedge_spans)
                if not near_hedge:
                    issues.append("Hypothesis language present but a later unqualified factual claim was not hedged")
                    risk_flags.append("hypothesis_promoted_to_fact")
                    break

        # Recursive-dependency guard: a flagged premise phrase must not
        # reappear later in the same response as an unqualified downstream
        # premise (e.g. "...does not exist. Therefore, the account was
        # never active."). Minimal heuristic, no claim-graph infrastructure.
        if risk_flags:
            flagged_fragments = [m.group(0) for m in strong_spans] + [
                m.group(0) for m in cls._UNAVAILABLE_AS_ABSENT_RE.finditer(response)
            ]
            for marker_match in cls._DEPENDENCY_MARKER_RE.finditer(response):
                preceding = response[: marker_match.start()]
                if any(frag.lower() in preceding.lower() for frag in flagged_fragments):
                    following = response[marker_match.end() : marker_match.end() + 200]
                    if following.strip() and not cls._HYPOTHESIS_RE.search(following):
                        issues.append("Later claim recursively depends on an earlier unsupported state-promoted claim")
                        risk_flags.append("recursive_unsupported_claim_dependency")
                        break

        # Severity tiering: negative_existence_without_evidence and
        # connector_unknown_collapsed_to_false are as serious as a
        # fabrication finding (matches tool_evidence_integrity's 0.2
        # penalty -- pushes confidence below the 0.45 "block" threshold in
        # _decide). unsupported_state_promotion/hypothesis_promoted_to_fact/
        # recursive_unsupported_claim_dependency are softer, landing in the
        # 0.45-0.75 "warn" range -- still risky enough that Phase 6 gates
        # memory commit on them, but not an automatic hard block.
        _SEVERE_FLAGS = {"negative_existence_without_evidence", "connector_unknown_collapsed_to_false"}
        if set(risk_flags) & _SEVERE_FLAGS:
            confidence_penalty = 0.25
        elif risk_flags:
            confidence_penalty = 0.7
        else:
            confidence_penalty = 1.0

        return {
            "passed": not issues,
            "issues": issues,
            "confidence_penalty": confidence_penalty,
            "risk_flags": risk_flags,
        }

    @classmethod
    def _check_vision_evidence_integrity(cls, context: ValidationContext) -> Dict[str, Any]:
        """Forbid visual perception claims when image inference did not produce evidence."""
        if "vision" not in set(context.tools_used or []):
            return {"passed": True, "issues": [], "confidence_penalty": 1.0}
        successful = cls._successful_tool_outputs(context.tool_outputs)
        has_vision_evidence = "vision" in successful
        response = context.response or ""
        perception_claim = bool(re.search(
            r"\b(?:auf|in)\s+dem\s+bild\b|\bdas\s+bild\s+(?:zeigt|enth[aä]lt)|"
            r"\bich\s+sehe\b|\bthe\s+image\s+(?:shows|contains)|\bi\s+can\s+see\b",
            response,
            flags=re.IGNORECASE,
        ))
        issues = []
        if perception_claim and not has_vision_evidence:
            issues.append("Fabricated visual perception claim: no successful vision evidence is available")
        return {
            "passed": not issues,
            "issues": issues,
            "confidence_penalty": 0.2 if issues else 1.0,
        }

    @staticmethod
    def _check_source_attribution(context: ValidationContext) -> Dict[str, Any]:
        """Verify response cites tool outputs when making claims."""
        issues = []

        tools_used = set(ResponseValidator._successful_tool_outputs(context.tool_outputs))
        needs_attribution = bool(tools_used & ResponseValidator._ATTRIBUTION_REQUIRED_TOOLS)

        if needs_attribution and not ResponseValidator._has_knowledge_reference_marker(context.response):
            issues.append("Response makes claims without source attribution")

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "confidence_penalty": 0.8 if issues else 1.0,
        }

    @staticmethod
    def _check_consistency(context: ValidationContext) -> Dict[str, Any]:
        """Verify response is consistent with tool outputs."""
        issues = []
        response_text = (context.response or "").lower()
        outputs = context.tool_outputs or {}

        has_results = False
        no_results = False
        for value in outputs.values():
            if isinstance(value, dict):
                count = value.get("count")
                if isinstance(count, int):
                    has_results = has_results or count > 0
                    no_results = no_results or count == 0
                items = value.get("items")
                if isinstance(items, list):
                    has_results = has_results or len(items) > 0
                    no_results = no_results or len(items) == 0

        response_denies_results = any(token in response_text for token in {
            "kein treffer",
            "keine treffer",
            "no results",
            "no evidence",
            "keine daten",
        })
        response_claims_results = any(token in response_text for token in {
            "treffer",
            "ergebnis",
            "results",
            "found",
            "gefunden",
        })

        if has_results and response_denies_results:
            issues.append("Severe contradiction between tool results and response")
            return {
                "passed": False,
                "issues": issues,
                "confidence_penalty": 0.4,
            }

        if no_results and response_claims_results:
            issues.append("Response claims tool hits while tool output indicates no results")

        # Basic self-contradiction heuristic.
        if "keine imagin" in response_text and "komplex" in response_text:
            issues.append("Response contains internal contradiction")

        # Generic factual self-check: for explicit year/date questions with tool evidence,
        # ensure response years align with years present in retrieved evidence.
        query_text = context.original_query or ""
        if ResponseValidator._YEAR_QUERY_RE.search(query_text) and outputs:
            evidence_years = ResponseValidator._extract_years_from_tool_outputs(outputs)
            response_years = ResponseValidator._extract_years_from_text(context.response or "")
            if evidence_years and response_years and evidence_years.isdisjoint(response_years):
                issues.append(
                    "Severe contradiction between tool evidence and response year values"
                )
                return {
                    "passed": False,
                    "issues": issues,
                    "confidence_penalty": 0.45,
                }

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "confidence_penalty": 0.75 if issues else 1.0,
        }

    @classmethod
    def _extract_years_from_text(cls, text: str) -> set[str]:
        return set(cls._YEAR_RE.findall(text or ""))

    @classmethod
    def _extract_years_from_tool_outputs(cls, outputs: Dict[str, Any]) -> set[str]:
        chunks: list[str] = []

        def collect(obj: Any) -> None:
            if obj is None:
                return
            if isinstance(obj, str):
                chunks.append(obj)
                return
            if isinstance(obj, dict):
                for key in ("summary_text", "output", "content", "query"):
                    value = obj.get(key)
                    if isinstance(value, str):
                        chunks.append(value)
                for value in obj.values():
                    collect(value)
                return
            if isinstance(obj, list):
                for item in obj:
                    collect(item)

        collect(outputs)
        years: set[str] = set()
        for chunk in chunks:
            years.update(cls._YEAR_RE.findall(chunk))
        return years

    @classmethod
    def _extract_graph_relations(cls, context: ValidationContext) -> list[dict[str, str]]:
        relations: list[dict[str, str]] = []
        for item in getattr(context, "graph_relations", []) or []:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source") or "").strip()
            relation = str(item.get("relation") or "").strip()
            target = str(item.get("target") or "").strip()
            if source and relation and target:
                relations.append({"source": source, "relation": relation, "target": target})

        for match in cls._GRAPH_RELATION_RE.finditer(getattr(context, "context_documents", "") or ""):
            relations.append(
                {
                    "source": match.group("source").strip(),
                    "relation": match.group("relation").strip(),
                    "target": match.group("target").strip(),
                }
            )
        return relations

    @staticmethod
    def _entity_aliases(value: str) -> set[str]:
        raw = (value or "").strip().lower()
        if not raw:
            return set()
        aliases = {raw}
        if ":" in raw:
            aliases.add(raw.rsplit(":", 1)[-1])
        aliases.add(raw.replace(":", " "))
        aliases.add(raw.replace("_", " ").replace("-", " "))
        return {alias for alias in aliases if len(alias) >= 3}

    @staticmethod
    def _relation_terms(relation: str) -> set[str]:
        normalized = (relation or "").strip().lower().replace("_", " ").replace("-", " ")
        terms = {normalized} if normalized else set()
        if normalized == "depends on":
            terms.update({
                "depends on",
                "depends",
                "haengt ab",
                "hängt ab",
                "abhaengig",
                "abhängig",
                "benoetigt",
                "benötigt",
            })
        if normalized == "next":
            terms.update({"next", "naechste", "nächste", "danach", "folgt"})
        if normalized == "summarizes":
            terms.update({"summarizes", "fasst zusammen", "summary", "zusammenfassung"})
        return {term for term in terms if term}

    @staticmethod
    def _contains_alias(text: str, aliases: set[str]) -> bool:
        return any(alias in text for alias in aliases)

    @staticmethod
    def _contains_negated_alias(text: str, aliases: set[str]) -> bool:
        for alias in aliases:
            escaped = re.escape(alias)
            if re.search(rf"\b(not|nicht|kein|keine|no)\b.{{0,48}}{escaped}", text):
                return True
            if re.search(rf"{escaped}.{{0,48}}\b(not|nicht|kein|keine|no)\b", text):
                return True
        return False

    @classmethod
    def _check_graph_priority(cls, context: ValidationContext) -> Dict[str, Any]:
        """Block answers that contradict concrete Neo4j relation evidence."""
        relations = cls._extract_graph_relations(context)
        if not relations:
            return {"passed": True, "issues": [], "confidence_penalty": 1.0}

        response = (context.response or "").lower()
        issues: list[str] = []

        for rel in relations:
            source_aliases = cls._entity_aliases(rel["source"])
            target_aliases = cls._entity_aliases(rel["target"])
            relation_terms = cls._relation_terms(rel["relation"])

            source_present = cls._contains_alias(response, source_aliases)
            target_present = cls._contains_alias(response, target_aliases)
            relation_present = cls._contains_alias(response, relation_terms)

            if source_present and relation_present and not target_present:
                issues.append(
                    "Severe contradiction: response overrides or omits authoritative graph relation "
                    f"{rel['source']} -[{rel['relation']}]-> {rel['target']}"
                )
                continue

            if source_present and relation_present and target_present and cls._contains_negated_alias(response, target_aliases):
                issues.append(
                    "Severe contradiction: response negates authoritative graph relation "
                    f"{rel['source']} -[{rel['relation']}]-> {rel['target']}"
                )

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "confidence_penalty": 0.35 if issues else 1.0,
        }

    @staticmethod
    def _check_grounding(context: ValidationContext) -> Dict[str, Any]:
        """Verify factual claims have evidence in tools or context sources."""
        issues = []
        query = (context.original_query or "").strip().lower()
        response = (context.response or "").strip().lower()
        successful_outputs = ResponseValidator._successful_tool_outputs(context.tool_outputs)
        tools_used = set(successful_outputs)
        needs_attribution = bool(tools_used & ResponseValidator._ATTRIBUTION_REQUIRED_TOOLS)

        fact_query = bool(
            re.match(r"^(was ist|what is|define|wer ist|who is)", query)
            or ResponseValidator._TOOL_DIRECTED_FACT_QUERY_RE.match(query)
        )
        source_total = sum(int(v or 0) for v in (context.context_sources or {}).values())
        tool_evidence = 1 if ResponseValidator._successful_tool_outputs(context.tool_outputs) else 0
        evidence_strength = source_total + tool_evidence

        has_uncertainty = any(token in response for token in {
            "ich bin nicht sicher",
            "unsicher",
            "wahrscheinlich",
            "could",
            "might",
            "not sure",
            "konnte nicht",
            "nicht ausgeführt",
            "nicht ausgefuehrt",
            "tool failed",
        })
        strong_assertion = any(token in response for token in {
            " ist ",
            " sind ",
            " is ",
            " are ",
            " bedeutet ",
            " means ",
            " liefert ",
            " lieferte ",
            " ergab ",
            " returned ",
            " found ",
        })

        # Only penalise for missing grounding when the router actually selected tools
        # but returned no evidence. Pure conversational answers (tools_used == empty)
        # are intentionally toolless and should not be flagged as ungrounded.
        router_requested_tools = bool(context.tools_used)
        if fact_query and evidence_strength == 0 and strong_assertion and not has_uncertainty and router_requested_tools:
            issues.append("Factual answer appears ungrounded: no context or tool evidence")
            return {
                "passed": False,
                "issues": issues,
                "confidence_penalty": 0.65,
            }

        if evidence_strength > 0 and needs_attribution and not ResponseValidator._has_knowledge_reference_marker(response):
            issues.append("Grounding evidence exists but source linkage is missing")

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "confidence_penalty": 0.85 if issues else 1.0,
        }

    @staticmethod
    def _check_safety(context: ValidationContext) -> Dict[str, Any]:
        """Baseline safety/policy checks for obvious risky outputs."""
        response = (context.response or "").lower()
        issues = []

        unsafe_markers = {
            "build a bomb",
            "baue eine bombe",
            "write malware",
            "entwickle malware",
            "bypass password",
            "exploit",
            "credential stuffing",
        }
        leak_markers = {
            "system prompt",
            "api key",
            "secret token",
            "internal instruction",
        }

        if any(marker in response for marker in unsafe_markers):
            issues.append("Unsafe content detected")
        if any(marker in response for marker in leak_markers):
            issues.append("Policy/security issue: internal secret disclosure risk")

        return {
            "passed": len(issues) == 0,
            "issues": issues,
            "confidence_penalty": 0.3 if issues else 1.0,
        }

    @staticmethod
    def _check_fast_check(context: ValidationContext) -> Dict[str, Any]:
        """Verify minimum response viability (empty/length/format sanity)."""
        response = context.response or ""
        stripped = response.strip()

        if not stripped:
            return {
                "passed": False,
                "issues": ["Response empty"],
                "confidence_penalty": 0.3,
            }

        if len(stripped) < 10:
            return {
                "passed": False,
                "issues": ["Response too short"],
                "confidence_penalty": 0.5,
            }

        if len(stripped) > 10000:
            return {
                "passed": False,
                "issues": ["Response too long"],
                "confidence_penalty": 0.8,
            }

        # Basic markdown sanity: unmatched fenced blocks are usually rendering errors.
        if stripped.count("```") % 2 != 0:
            return {
                "passed": False,
                "issues": ["Malformed markdown code fence"],
                "confidence_penalty": 0.8,
            }

        # If it looks like JSON, ensure it is parseable.
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                json.loads(stripped)
            except Exception:
                return {
                    "passed": False,
                    "issues": ["Invalid JSON format"],
                    "confidence_penalty": 0.7,
                }

        return {
            "passed": True,
            "issues": [],
            "confidence_penalty": 1.0,
        }

    @staticmethod
    def _check_command_response_mismatch(context: ValidationContext) -> Dict[str, Any]:
        """Verify slash-commands do not receive free-form LLM text responses."""
        query = (context.original_query or "").strip()
        response = (context.response or "").strip()

        is_slash_command = query.startswith("/")
        if not is_slash_command:
            return {
                "passed": True,
                "issues": [],
                "confidence_penalty": 1.0,
            }

        # Slash-commands should produce structured output, not lengthy free-form LLM text
        # Heuristic: if response is very long or contains multiple sentences, likely LLM-generated
        response_length = len(response)
        is_lengthy_response = response_length > 200
        has_multiple_sentences = response.count(".") >= 2 or response.count("\n") >= 2
        looks_like_llm_text = is_lengthy_response or has_multiple_sentences

        if looks_like_llm_text:
            return {
                "passed": False,
                "issues": ["Slash-command input received LLM-generated text instead of structured response"],
                "confidence_penalty": 0.1,  # Very high penalty for command mismatch
            }

        return {
            "passed": True,
            "issues": [],
            "confidence_penalty": 1.0,
        }
