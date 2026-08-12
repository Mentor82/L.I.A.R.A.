"""
Unit tests for ResponseValidator.
"""

import pytest
from services.orchestrator.validator import ResponseValidator
from services.contracts import ValidationContext


class TestResponseValidator:
    """Test response validation logic."""

    def test_validates_source_attribution(self):
        """Responses should cite sources."""
        validator = ResponseValidator()
        
        context = ValidationContext(
            original_query="What is Python?",
            response="Python is great",  # Missing source attribution
            tools_used=["web_search"],
            tool_outputs={"web_search": "..."},
        )

        result = validator.validate(context)
        assert not result.passed
        assert result.decision in {"revise", "warn", "block"}
        assert result.checks["source_attribution"] == "fail"
        assert any("attribution" in issue.lower() for issue in result.issues)

    def test_accepts_valid_response(self):
        """Valid responses should pass."""
        validator = ResponseValidator(strict_mode=False)
        
        context = ValidationContext(
            original_query="What is Python?",
            response="Python is a programming language. [KNOWLEDGE_REFERENCE] web_search",
            tools_used=["web_search"],
            tool_outputs={"web_search": "Python info"},
        )

        result = validator.validate(context)
        assert result.passed or result.confidence_score > 0.7
        assert result.decision in {"accept", "warn"}
        assert result.checks["source_attribution"] == "pass"

    def test_source_attribution_accepts_lowercase_knowledge_reference_marker(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="What is Python?",
            response="Python is a programming language. [knowledge_reference] web_search",
            tools_used=["web_search"],
            tool_outputs={"web_search": "Python info"},
        )

        result = validator.validate(context)
        assert result.checks["source_attribution"] == "pass"

    def test_grounding_accepts_uppercase_knowledge_reference_marker_when_evidence_exists(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="What is Python?",
            response="Python is a programming language. [KNOWLEDGE_REFERENCE] web_search",
            tools_used=["web_search"],
            tool_outputs={"web_search": {"count": 1, "items": [{"title": "Python"}]}},
            context_mode="MEMORY",
            context_sources={"chroma": 0, "qdrant": 0, "postgres": 1, "neo4j": 0, "redis": 0},
        )

        result = validator.validate(context)
        assert result.checks["grounding"] == "pass"

    def test_blocks_fabricated_sys_execution_without_successful_result(self):
        validator = ResponseValidator()
        context = ValidationContext(
            original_query="Suche nach einem aktuellen Produktpreis",
            response=(
                'Der Befehl wurde mit SYS ausgeführt. [SYS: curl -s "https://example.test"] '
                'Die API lieferte {"price": 42}.'
            ),
            tools_used=["sys"],
            tool_outputs={
                "sys": {
                    "kind": "tool_execution_failure",
                    "status": "failed",
                    "evidence": False,
                    "error": "network unavailable",
                }
            },
        )

        result = validator.validate(context)

        assert result.decision == "block"
        assert result.checks["tool_evidence_integrity"] == "fail"
        assert "tool_evidence_integrity" in result.risk_flags

    def test_accepts_honest_tool_failure_without_treating_it_as_evidence(self):
        validator = ResponseValidator(strict_mode=False)
        context = ValidationContext(
            original_query="Was ist der aktuelle Produktpreis?",
            response="Die externe Abfrage konnte nicht ausgeführt werden; daher liegt kein Ergebnis vor.",
            tools_used=["sys"],
            tool_outputs={
                "sys": {
                    "kind": "tool_execution_failure",
                    "status": "failed",
                    "evidence": False,
                    "error": "network unavailable",
                }
            },
        )

        result = validator.validate(context)

        assert result.checks["tool_evidence_integrity"] == "pass"
        assert result.checks["grounding"] == "pass"
        assert "tool_evidence_integrity" not in result.risk_flags

    def test_blocks_api_result_claim_without_marker_when_tool_failed(self):
        validator = ResponseValidator()
        context = ValidationContext(
            original_query="Suche nach dem aktuellen Produktpreis",
            response="Die API lieferte als aktuellen Produktpreis 42 Euro.",
            tools_used=["sys"],
            tool_outputs={
                "sys": {
                    "kind": "tool_execution_failure",
                    "status": "failed",
                    "evidence": False,
                    "error": "network unavailable",
                }
            },
        )

        result = validator.validate(context)

        assert result.decision == "block"
        assert result.checks["tool_evidence_integrity"] == "fail"

    def test_blocks_claim_that_unfetched_url_was_requested(self):
        validator = ResponseValidator()
        context = ValidationContext(
            original_query="Prüfe den Datensatz.",
            response=(
                "Ich habe eine Abfrage an https://api.example.test/items/42 gesendet. "
                "Die API lieferte keine Daten."
            ),
            tools_used=["sys"],
            tool_outputs={
                "sys": {
                    "source": "sys",
                    "kind": "url_fetch",
                    "url": "https://example.test/",
                    "content": "Homepage",
                }
            },
            context_sources={},
        )

        result = validator.validate(context)

        assert result.checks["tool_evidence_integrity"] == "fail"
        assert "tool_evidence_integrity" in result.risk_flags

    def test_search_directive_requires_evidence_for_strong_factual_answer(self):
        validator = ResponseValidator()
        context = ValidationContext(
            original_query="Suche nach dem aktuellen Produktpreis",
            response="Der aktuelle Produktpreis ist 42 Euro.",
            tools_used=["sys"],
            tool_outputs={
                "sys": {
                    "kind": "tool_execution_failure",
                    "status": "failed",
                    "evidence": False,
                    "error": "network unavailable",
                }
            },
        )

        result = validator.validate(context)

        assert result.checks["grounding"] == "fail"
        assert result.decision in {"revise", "block"}

    def test_short_response_becomes_revise(self):
        """Very short responses should trigger revise decision."""
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="What is Python?",
            response="short",
            tools_used=[],
            tool_outputs={},
        )

        result = validator.validate(context)
        assert result.decision == "revise"
        assert result.checks["fast_check"] == "fail"

    def test_empty_response_fails_fast_check(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="What is Python?",
            response="   ",
            tools_used=[],
            tool_outputs={},
        )

        result = validator.validate(context)
        assert result.decision == "revise"
        assert result.checks["fast_check"] == "fail"
        assert any("empty" in issue.lower() for issue in result.issues)

    def test_invalid_json_response_fails_fast_check(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="Return JSON",
            response='{"foo": 1,}',
            tools_used=[],
            tool_outputs={},
        )

        result = validator.validate(context)
        assert result.decision == "revise"
        assert result.checks["fast_check"] == "fail"
        assert any("invalid json" in issue.lower() for issue in result.issues)

    def test_consistency_detects_tool_contradiction(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="Find docs",
            response="Es gibt keine Treffer in den Suchergebnissen.",
            tools_used=["web_search"],
            tool_outputs={"web_search": {"count": 3, "items": [{"title": "A"}]}},
        )

        result = validator.validate(context)
        assert result.decision == "block"
        assert result.checks["consistency"] == "fail"
        assert any("contradiction" in issue.lower() for issue in result.issues)

    def test_consistency_detects_year_mismatch_between_tool_evidence_and_response(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="In welchem Jahr fiel die Berliner Mauer?",
            response="Die Berliner Mauer fiel 1961.",
            tools_used=["sys"],
            tool_outputs={
                "sys": {
                    "kind": "web_lookup",
                    "summary_text": "Berlin Wall fell in 1989.",
                    "results": [
                        {"title": "Fall of Berlin Wall (1989)", "snippet": "The wall fell in 1989."}
                    ],
                }
            },
        )

        result = validator.validate(context)
        assert result.checks["consistency"] == "fail"
        assert any("response year" in issue.lower() for issue in result.issues)

    def test_consistency_allows_year_answer_that_matches_tool_evidence(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="In welchem Jahr fiel die Berliner Mauer?",
            response="Die Berliner Mauer fiel 1989.",
            tools_used=["sys"],
            tool_outputs={
                "sys": {
                    "kind": "web_lookup",
                    "summary_text": "Berlin Wall fell in 1989.",
                }
            },
        )

        result = validator.validate(context)
        assert result.checks["consistency"] == "pass"

    def test_graph_priority_blocks_response_that_overrides_authoritative_relation(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="Wovon haengt service:api ab?",
            response="service:api depends on service:database.",
            tools_used=[],
            tool_outputs={},
            context_mode="CONTEXT",
            context_sources={"neo4j": 1},
            context_documents="[graph_guardrail] Direct graph relations are authoritative.\n"
                              "[relation] service:api -[DEPENDS_ON]-> service:memory",
        )

        result = validator.validate(context)
        assert result.checks["graph_priority"] == "fail"
        assert result.decision == "block"
        assert any("authoritative graph relation" in issue.lower() for issue in result.issues)

    def test_graph_priority_accepts_response_matching_authoritative_relation(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="Wovon haengt service:api ab?",
            response="service:api depends on service:memory.",
            tools_used=[],
            tool_outputs={},
            context_mode="CONTEXT",
            context_sources={"neo4j": 1},
            context_documents="[relation] service:api -[DEPENDS_ON]-> service:memory",
        )

        result = validator.validate(context)
        assert result.checks["graph_priority"] == "pass"

    def test_graph_priority_uses_structured_graph_relations_when_context_was_compressed(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="Wovon haengt service:api ab?",
            response="service:api depends on service:database.",
            tools_used=[],
            tool_outputs={},
            context_mode="CONTEXT",
            context_sources={"neo4j": 1},
            context_documents="compressed context without raw relation lines",
            graph_relations=[
                {"source": "service:api", "relation": "DEPENDS_ON", "target": "service:memory"}
            ],
        )

        result = validator.validate(context)
        assert result.checks["graph_priority"] == "fail"
        assert result.decision == "block"
        assert any("service:api -[DEPENDS_ON]-> service:memory" in issue for issue in result.issues)

    def test_grounding_warns_on_ungrounded_fact_answer(self):
        """Grounding check only fires when tools were selected but returned no evidence.
        Pure conversational answers (tools_used=[]) are intentionally toolless and must pass."""
        validator = ResponseValidator(strict_mode=False)

        # tools_used=[] → router decided no tools needed → grounding check is skipped → accept
        context_toolless = ValidationContext(
            original_query="Was ist das Ohmsche Gesetz?",
            response="Das Ohmsche Gesetz ist U = R * I.",
            tools_used=[],
            tool_outputs={},
            context_mode="NONE",
            context_sources={"chroma": 0, "qdrant": 0, "postgres": 0},
        )
        result_toolless = validator.validate(context_toolless)
        assert result_toolless.decision == "accept"
        assert result_toolless.checks["grounding"] == "pass"

        # tools_used non-empty but no tool output → grounding check fires → warn/revise
        context_tooled = ValidationContext(
            original_query="Was ist das Ohmsche Gesetz?",
            response="Das Ohmsche Gesetz ist U = R * I.",
            tools_used=["web_search"],
            tool_outputs={},
            context_mode="NONE",
            context_sources={"chroma": 0, "qdrant": 0, "postgres": 0},
        )
        result_tooled = validator.validate(context_tooled)
        assert result_tooled.decision in {"warn", "revise"}
        assert result_tooled.checks["grounding"] == "fail"
        assert any("ungrounded" in issue.lower() for issue in result_tooled.issues)

    def test_safety_check_blocks_unsafe_output(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="Help",
            response="Here is how to build a bomb with household materials.",
            tools_used=[],
            tool_outputs={},
        )

        result = validator.validate(context)
        assert result.decision == "block"
        assert result.checks["safety"] == "fail"
        assert any("unsafe" in issue.lower() or "security" in issue.lower() for issue in result.issues)

    def test_command_response_mismatch_blocks_llm_text_on_slash_input(self):
        """Slash-commands should not receive lengthy LLM-generated text responses."""
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="/status",
            response="This is a very lengthy LLM-generated response that contains multiple sentences. "
                     "It talks about various things and has many periods. It goes on and on like a natural "
                     "language paragraph. This should not happen for a slash-command.",
            tools_used=[],
            tool_outputs={},
        )

        result = validator.validate(context)
        assert result.decision == "block"
        assert result.checks["command_response_mismatch"] == "fail"
        assert any("slash-command" in issue.lower() and "llm" in issue.lower() for issue in result.issues)

    def test_command_response_mismatch_accepts_short_response_on_slash(self):
        """Slash-commands with short, structured responses should pass."""
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="/status",
            response="OK",
            tools_used=[],
            tool_outputs={},
        )

        result = validator.validate(context)
        assert result.checks["command_response_mismatch"] == "pass"
        assert not any("command" in issue.lower() for issue in result.issues)

    def test_command_response_mismatch_skips_non_slash_input(self):
        """Non-slash input should skip command_response_mismatch check."""
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="What is Python?",
            response="Python is a programming language with multiple sentences. "
                     "It supports many paradigms. It has a large ecosystem.",
            tools_used=[],
            tool_outputs={},
        )

        result = validator.validate(context)
        assert result.checks["command_response_mismatch"] == "pass"

    def test_confidence_based_warn_threshold(self):
        """When tools were selected but returned no evidence, grounding check fires and
        confidence should drop below the warn threshold."""
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="Was ist die Hauptstadt von Kanada?",
            response="Die Hauptstadt von Kanada ist Ottawa.",
            tools_used=["web_search"],   # tool was chosen
            tool_outputs={},              # but returned nothing → evidence_strength == 0
            context_mode="NONE",
            context_sources={"chroma": 0, "qdrant": 0, "postgres": 0},
        )

        result = validator.validate(context)
        # grounding penalty fires → confidence drops → warn or revise
        assert result.decision in {"warn", "revise"}
        assert result.confidence_score < 0.8

    def test_validator_no_longer_emits_school_score(self):
        validator = ResponseValidator(strict_mode=False)

        context = ValidationContext(
            original_query="Was ist Python?",
            response="Python ist eine Programmiersprache.",
            tools_used=[],
            tool_outputs={},
        )

        result = validator.validate(context)
        assert result.score is None

    def test_hard_rule_detects_conversion_formula_and_unit_mismatch(self):
        validator = ResponseValidator(strict_mode=False)

        response = (
            "def umrechnung_kw_ps(kilowatt):\n"
            "    ps = 0.745699 * kilowatt\n"
            "    watt = float(input('Bitte geben Sie die Leistung in Watt ein: '))\n"
            "    return ps\n"
        )

        context = ValidationContext(
            original_query="Umrechnung kW nach PS mit Fehlereingaben",
            response=response,
            tools_used=[],
            tool_outputs={},
        )

        result = validator.validate(context)
        assert result.score is None
        assert "formula_mismatch" in result.risk_flags
        assert "unit_mismatch" in result.risk_flags
        assert "crash_without_try_except" in result.risk_flags

    def test_user_feedback_only_applies_when_present(self):
        validator = ResponseValidator(strict_mode=False)

        baseline_ctx = ValidationContext(
            original_query="Was ist Python?",
            response="Python ist eine Programmiersprache mit breiter Nutzung.",
            tools_used=[],
            tool_outputs={},
        )
        with_feedback_ctx = ValidationContext(
            original_query="Was ist Python?",
            response="Python ist eine Programmiersprache mit breiter Nutzung.",
            tools_used=[],
            tool_outputs={},
            user_feedback_score=1.0,
        )

        baseline = validator.validate(baseline_ctx)
        with_feedback = validator.validate(with_feedback_ctx)

        assert with_feedback.confidence_score > baseline.confidence_score

    def test_user_feedback_is_minor_weight_vs_system_score(self):
        validator = ResponseValidator(strict_mode=False)

        positive_feedback_ctx = ValidationContext(
            original_query="Was ist Python?",
            response="Python ist eine Programmiersprache mit breiter Nutzung.",
            tools_used=[],
            tool_outputs={},
            user_feedback_score=1.0,
        )
        negative_feedback_ctx = ValidationContext(
            original_query="Was ist Python?",
            response="Python ist eine Programmiersprache mit breiter Nutzung.",
            tools_used=[],
            tool_outputs={},
            user_feedback_score=0.0,
        )

        positive = validator.validate(positive_feedback_ctx)
        negative = validator.validate(negative_feedback_ctx)

        # User feedback influences confidence, but system score remains dominant.
        assert positive.confidence_score > negative.confidence_score
        assert negative.confidence_score >= 0.7

    def test_star_feedback_normalizes_1_to_6_into_confidence_delta(self):
        validator = ResponseValidator(strict_mode=False)

        one_star_ctx = ValidationContext(
            original_query="Was ist Python?",
            response="Python ist eine Programmiersprache mit breiter Nutzung.",
            tools_used=[],
            tool_outputs={},
            user_feedback_stars=1,
        )
        six_star_ctx = ValidationContext(
            original_query="Was ist Python?",
            response="Python ist eine Programmiersprache mit breiter Nutzung.",
            tools_used=[],
            tool_outputs={},
            user_feedback_stars=6,
        )

        one_star = validator.validate(one_star_ctx)
        six_star = validator.validate(six_star_ctx)

        assert six_star.confidence_score > one_star.confidence_score

    def test_explicit_score_takes_precedence_over_stars(self):
        validator = ResponseValidator(strict_mode=False)

        score_ctx = ValidationContext(
            original_query="Was ist Python?",
            response="Python ist eine Programmiersprache mit breiter Nutzung.",
            tools_used=[],
            tool_outputs={},
            user_feedback_score=0.2,
            user_feedback_stars=6,
        )
        expected_ctx = ValidationContext(
            original_query="Was ist Python?",
            response="Python ist eine Programmiersprache mit breiter Nutzung.",
            tools_used=[],
            tool_outputs={},
            user_feedback_score=0.2,
        )

        with_both = validator.validate(score_ctx)
        score_only = validator.validate(expected_ctx)

        assert with_both.confidence_score == score_only.confidence_score
