"""
Unit tests for ADR-007 Epistemic Schema and 4-Class Relations.
"""

import pytest
from ai_brain.schema import (
    EpistemicState,
    SemanticRelation,
    EvolutionRelation,
    SystemRelation,
    PersonalDenkraumRelation,
    BrainNode,
    BrainEdge,
    EdgeProvenance,
)


def test_epistemic_states_defined():
    assert EpistemicState.USER_CONFIRMED.value == "USER_CONFIRMED"
    assert EpistemicState.VERIFIED.value == "VERIFIED"
    assert EpistemicState.INFERENCE.value == "INFERENCE"
    assert EpistemicState.HYPOTHESIS.value == "HYPOTHESIS"
    assert EpistemicState.CONTRADICTED.value == "CONTRADICTED"
    assert EpistemicState.SUPERSEDED.value == "SUPERSEDED"


def test_four_relation_classes_defined():
    assert SemanticRelation.RELATES_TO.value == "RELATES_TO"
    assert EvolutionRelation.EVOLVED_INTO.value == "EVOLVED_INTO"
    assert SystemRelation.IMPLEMENTS.value == "IMPLEMENTS"
    assert PersonalDenkraumRelation.INSPIRED_BY.value == "INSPIRED_BY"


def test_brain_edge_provenance_defaults():
    edge = BrainEdge(
        id="edge_1",
        subject_id="node_a",
        predicate="INSPIRED_BY",
        object_id="node_b",
        epistemic_state=EpistemicState.USER_CONFIRMED,
        provenance=EdgeProvenance(
            source_type="user_confirmed",
            confidence=1.0,
            verified=True,
            scope="projects:read",
        ),
    )
    assert edge.epistemic_state == EpistemicState.USER_CONFIRMED
    assert edge.provenance.confidence == 1.0
    assert edge.provenance.verified is True
