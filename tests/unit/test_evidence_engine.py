from services.orchestrator.evidence_engine import EvidenceEngine


def test_select_sources_does_not_match_age_inside_language():
    engine = EvidenceEngine()

    selection = engine._select_sources(
        query="Wie muesste ein cross-language call zwischen Julia und Python aussehen?",
        required_level="medium",
        source_counts={"facts": 0, "qdrant": 0, "chroma": 0},
        tool_outputs={},
        has_history=False,
    )

    assert "facts" not in selection["selected_sources"]
    assert selection["selected_sources"] == ["user_input_only"]


def test_decompose_design_query_uses_medium_evidence_level():
    engine = EvidenceEngine()

    decomposition = engine._decompose_query(
        "Analysiere systemisch, warum ein hybrider Julia/Python-Ansatz mit SIMD, GIL und LLVM-JIT fuer ein Systemdesign sinnvoll ist."
    )

    assert decomposition["required_evidence_level"] == "medium"


def test_low_evidence_self_description_keeps_canonical_system_context():
    engine = EvidenceEngine()

    result = engine.analyze(
        query="Describe LIARA in one short sentence.",
        context_channels={
            "system_context": (
                "Identity: Liara (AI-Orchestrator and Agent)\n"
                "Description: Liara is a structured deterministic AI orchestrator."
            )
        },
        source_counts={"system": 1, "facts": 0, "qdrant": 0, "chroma": 0},
        tool_outputs={},
        conversation_history="",
    )

    assert "system" in result.selected_sources
    assert any(item.get("source") == "system" for item in result.evidence_items)
    assert "Liara" in result.evidence_context
