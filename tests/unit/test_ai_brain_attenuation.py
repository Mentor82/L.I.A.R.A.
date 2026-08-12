"""
Unit tests for AI-Brain Capability Attenuation (ADR-007 Invariant 2).
Rule: Capabilities may attenuate, NEVER amplify.
"""

import pytest
from ai_brain.auth import VisitorPassAuthManager
from ai_brain.schema import EpistemicState


def test_issue_parent_and_valid_attenuated_pass():
    auth_mgr = VisitorPassAuthManager()
    parent = auth_mgr.issue_pass(
        subject="agent_parent",
        scopes=["facts:read", "relations:read", "projects:read"],
        allowed_epistemic_states=[
            EpistemicState.USER_CONFIRMED,
            EpistemicState.VERIFIED,
            EpistemicState.INFERENCE,
        ],
        max_hops=3,
        ttl_seconds=1800,
    )

    # Valid attenuation: smaller scope subset, smaller max_hops
    sub = auth_mgr.attenuate_pass(
        parent_token_id=parent.token_id,
        sub_subject="agent_sub",
        sub_scopes=["facts:read"],
        sub_max_hops=2,
    )

    assert sub.scopes == ["facts:read"]
    assert sub.max_hops == 2
    assert sub.subject == "agent_sub"
    assert sub.audience == parent.audience


def test_attenuation_violation_scopes_raises_error():
    auth_mgr = VisitorPassAuthManager()
    parent = auth_mgr.issue_pass(
        subject="agent_parent",
        scopes=["facts:read"],
        max_hops=2,
    )

    # Attempt to amplify scopes (add "projects:read")
    with pytest.raises(ValueError, match="Attenuation violation"):
        auth_mgr.attenuate_pass(
            parent_token_id=parent.token_id,
            sub_subject="agent_sub",
            sub_scopes=["facts:read", "projects:read"],
        )


def test_attenuation_violation_max_hops_raises_error():
    auth_mgr = VisitorPassAuthManager()
    parent = auth_mgr.issue_pass(
        subject="agent_parent",
        scopes=["facts:read"],
        max_hops=2,
    )

    # Attempt to amplify max_hops from 2 to 4
    with pytest.raises(ValueError, match="Attenuation violation"):
        auth_mgr.attenuate_pass(
            parent_token_id=parent.token_id,
            sub_subject="agent_sub",
            sub_max_hops=4,
        )
