"""Import compatibility and method delegation test suite for Orchestrator refactoring."""

import pytest
from services.orchestrator import (
    Orchestrator,
    QueryRouter,
    QueryPlanner,
    ToolExecutor,
    ResponseValidator,
    RunStateManager,
)
from services.orchestrator.reasoning_control import resolve_reasoning_threshold_profile
from services.orchestrator.librarian_pipeline import retrieval_rerank
from services.orchestrator.tool_discovery import select_tools
from services.orchestrator.generation_pipeline import apply_empty_response_fallback


def test_orchestrator_package_imports():
    """Verify that all public orchestrator package exports remain directly importable."""
    assert Orchestrator is not None
    assert QueryRouter is not None
    assert QueryPlanner is not None
    assert ToolExecutor is not None
    assert ResponseValidator is not None
    assert RunStateManager is not None


def test_orchestrator_instantiation_and_delegation_methods():
    """Verify that Orchestrator instance retains all delegating helper methods."""
    orch = Orchestrator()
    assert hasattr(orch, "run")
    assert hasattr(orch, "_select_tools")
    assert hasattr(orch, "_build_prompt")
    assert hasattr(orch, "_validate_response")
    assert hasattr(orch, "_retrieval_rerank")
    assert hasattr(orch, "_compute_belief_snapshot")
    assert hasattr(orch, "_resolve_reasoning_threshold_profile")

    # Test direct facade method delegation execution
    profile = orch._resolve_reasoning_threshold_profile(session_id="test-session-001")
    assert isinstance(profile, dict)

    reranked = orch._retrieval_rerank(query="test", candidates=[{"content": "hello world", "similarity": 0.9}])
    assert isinstance(reranked, list)
    assert len(reranked) == 1

    fallback = orch._apply_empty_response_fallback(input_profile=None, query="Hello world test")
    assert isinstance(fallback, str)
    assert "Liara" in fallback


def test_submodule_direct_functions():
    """Verify direct submodule function calls work cleanly."""
    profile = resolve_reasoning_threshold_profile(Orchestrator(), session_id="sess-002")
    assert isinstance(profile, dict)

    reranked = retrieval_rerank(Orchestrator(), query="query", candidates=[])
    assert reranked == []
