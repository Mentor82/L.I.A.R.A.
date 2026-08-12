"""Unit tests for Scout Embedding Integration and Vector-based Semantic Routing."""

from __future__ import annotations

from types import SimpleNamespace
import numpy as np
import pytest

from services.contracts import RouterRequest
from services.orchestrator.router import QueryRouter
from services.orchestrator.scout_embedding import IntentProfile, ScoutEmbeddingClient


def test_intent_profile_text_representation():
    profile = IntentProfile(
        name="debugging",
        description="Error and stacktrace analysis",
        version="v1",
        anchors={"traceback", "error", "bug"},
    )
    text = profile.text_representation()
    assert "debugging" in text
    assert "Error and stacktrace analysis" in text
    assert "bug" in text
    assert "error" in text
    assert "traceback" in text


def test_cosine_similarity():
    vec_a = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    vec_b = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    vec_c = np.array([0.0, 1.0, 0.0], dtype=np.float32)

    assert ScoutEmbeddingClient.cosine_similarity(vec_a, vec_b) == pytest.approx(1.0)
    assert ScoutEmbeddingClient.cosine_similarity(vec_a, vec_c) == pytest.approx(0.0)
    assert ScoutEmbeddingClient.cosine_similarity(vec_a, np.array([0.0, 0.0, 0.0])) == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_scout_embedding_client_score_intents(monkeypatch):
    profiles = {
        "orientation": IntentProfile(
            name="orientation",
            description="Assistant orientation and capabilities",
            anchors={"who", "you"},
        ),
        "debugging": IntentProfile(
            name="debugging",
            description="Debugging and error analysis",
            anchors={"error", "exception"},
        ),
    }

    client = ScoutEmbeddingClient(
        redis_url=None,
        embedding_service_url="http://mock:8030",
        intent_profiles=profiles,
    )

    # Mock _embed_text to return predictable 3D vectors
    async def _mock_embed_text(text: str) -> np.ndarray:
        if "error" in text.lower() or "exception" in text.lower() or "debugging" in text.lower():
            return np.array([0.0, 1.0, 0.0], dtype=np.float32)
        return np.array([1.0, 0.0, 0.0], dtype=np.float32)

    monkeypatch.setattr(client, "_embed_text", _mock_embed_text)

    await client.initialize()
    assert client._ready is True

    scores = await client.score_intents("I have a python exception traceback error")
    assert "debugging" in scores
    assert "orientation" in scores
    assert scores["debugging"] > scores["orientation"]


@pytest.mark.asyncio
async def test_query_router_with_scout_embedding(monkeypatch):
    monkeypatch.setenv("SCOUT_USE_REAL_EMBEDDINGS", "true")
    router = QueryRouter()

    # Mock embedding client
    async def _mock_score_intents(query: str) -> dict[str, float]:
        if "fehler" in query.lower() or "error" in query.lower():
            return {
                "orientation": 0.1,
                "conversation_recall_local": 0.0,
                "sys": 0.2,
                "data_analysis": 0.1,
                "code_exploration": 0.2,
                "debugging": 0.92,
            }
        return {}

    fake_client = SimpleNamespace(
        _ready=True,
        score_intents=_mock_score_intents,
    )
    router._scout_embedding_client = fake_client

    decision = await router.route(RouterRequest(query="Zeige mir den Fehler im Log"))

    assert decision is not None
    assert decision.selected_tools == ["sys"]
    assert decision.reason == "semantic_embedding_debugging"
    assert decision.metadata.get("semantic_routing") is True
    assert decision.metadata.get("semantic_backend") == "embedding"
    assert decision.metadata.get("semantic_intent") == "debugging"
    assert decision.metadata.get("semantic_score") == 0.92
    assert "debugging" in decision.metadata.get("semantic_scores", {})


@pytest.mark.asyncio
async def test_query_router_fallback_when_embedding_disabled(monkeypatch):
    monkeypatch.setenv("SCOUT_USE_REAL_EMBEDDINGS", "false")
    router = QueryRouter()
    router._scout_embedding_client = None

    decision = await router.route(RouterRequest(query="wer bist du?"))
    assert decision is not None
    assert "orientation" in decision.selected_tools
