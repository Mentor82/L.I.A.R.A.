from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from services.api import create_api_app
from services.contracts import (
    GraphSubgraphEdge,
    GraphSubgraphNode,
    GraphSubgraphRequest,
    GraphSubgraphResponse,
    HeartbeatOperationsResponse,
    SelfObserverOperationsResponse,
    MemoryServiceStatus,
    OrchestratorResponse,
)
from services.memory import InMemoryMemoryServiceStore, create_memory_service_app
from services.memory.store import BackedMemoryServiceStore, EphemeralMemoryStore, NullMemoryStore
from services.memory.tier_store import GraphStore, MemoryLayer
from services.memory_adapter import InProcessMemoryAdapter


class _FakeOrchestrator:
    async def run(self, request):
        return OrchestratorResponse(
            run_id=request.run_id,
            final_response="unused",
            state_final="complete",
            llm_generation={},
            validation_result={},
            execution_trace=[],
        )


class _FakeGraphOperationsStore:
    closed = False

    async def architecture_subgraph(self, request):
        return GraphSubgraphResponse(
            component=request.component,
            nodes=[
                GraphSubgraphNode(
                    id="Agent:agent:orchestrator-v1",
                    label="Agent",
                    title="agent:orchestrator-v1",
                    properties={"id": "agent:orchestrator-v1", "role": "orchestrator"},
                ),
                GraphSubgraphNode(
                    id="Tool:sys",
                    label="Tool",
                    title="sys",
                    properties={"name": "sys"},
                ),
            ],
            edges=[
                GraphSubgraphEdge(
                    id="edge-1",
                    source="Agent:agent:orchestrator-v1",
                    target="Tool:sys",
                    relation="USES_TOOL",
                )
            ],
            query_ms=4,
        )

    async def close(self):
        self.closed = True


class _FakeDriver:
    def __init__(self, records):
        self.records = records
        self.query = ""
        self.params = {}

    async def execute_query(self, query, **params):
        self.query = query
        self.params = params
        return self.records, None, None


class _SlowArchitectureGraphStore:
    async def architecture_subgraph(self, **kwargs):
        del kwargs
        await asyncio.sleep(1.0)
        return {"nodes": [], "edges": [], "truncated": False, "query_ms": 1000}


def _adapter():
    return InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )


def test_operations_subgraph_is_read_only_bounded_and_typed(monkeypatch):
    fake_store = _FakeGraphOperationsStore()
    monkeypatch.setattr("services.api.app.BackedMemoryServiceStore", lambda: fake_store)
    app = create_api_app(orchestrator=_FakeOrchestrator(), memory_adapter=_adapter())

    with TestClient(app) as client:
        response = client.get(
            "/operations/graph/subgraph",
            params={"component": "orchestrator", "limit": 12},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["edges"][0]["relation"] == "USES_TOOL"
    assert fake_store.closed is True


@pytest.mark.parametrize(
    ("params", "expected_status"),
    [
        ({"component": "unknown"}, 422),
        ({"component": "memory", "limit": 26}, 422),
    ],
)
def test_operations_subgraph_rejects_unapproved_scope_or_limit(params, expected_status):
    app = create_api_app(orchestrator=_FakeOrchestrator(), memory_adapter=_adapter())
    with TestClient(app) as client:
        response = client.get("/operations/graph/subgraph", params=params)
    assert response.status_code == expected_status


def test_memory_service_exposes_typed_subgraph_route():
    app = create_memory_service_app(InMemoryMemoryServiceStore())
    with TestClient(app) as client:
        response = client.post(
            "/graph/architecture/subgraph",
            json={"component": "memory", "limit": 10},
        )
    assert response.status_code == 200
    assert response.json()["component"] == "memory"
    assert response.json()["status"]["status"] == "failed"


def test_operations_heartbeat_is_read_only_and_uses_canonical_proxy(monkeypatch):
    captured = {}

    async def fake_fetch(**kwargs):
        captured.update(kwargs)
        return HeartbeatOperationsResponse(
            status="success",
            service_health={"status": "ok", "sequence": 42},
        )

    monkeypatch.setattr("services.api.routers.operations._fetch_heartbeat_operations", fake_fetch)
    app = create_api_app(orchestrator=_FakeOrchestrator(), memory_adapter=_adapter())
    with TestClient(app) as client:
        response = client.get("/operations/heartbeat", params={"window_seconds": 60})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "success"
    assert captured["base_url"] == "http://127.0.0.1:8050"
    assert captured["window_seconds"] == 60


def test_operations_heartbeat_rejects_unbounded_window():
    app = create_api_app(orchestrator=_FakeOrchestrator(), memory_adapter=_adapter())
    with TestClient(app) as client:
        response = client.get("/operations/heartbeat", params={"window_seconds": 901})
    assert response.status_code == 422


def test_operations_self_observer_is_read_only_and_bounded(monkeypatch):
    captured = {}

    async def fake_fetch(**kwargs):
        captured.update(kwargs)
        return SelfObserverOperationsResponse(
            status="success",
            service_health={"status": "ok", "sequence": 7},
        )

    monkeypatch.setattr("services.api.routers.operations._fetch_self_observer_operations", fake_fetch)
    app = create_api_app(orchestrator=_FakeOrchestrator(), memory_adapter=_adapter())
    with TestClient(app) as client:
        response = client.get("/operations/self-observer", params={"history_limit": 12})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["status"] == "success"
    assert captured["base_url"] == "http://127.0.0.1:8060"
    assert captured["history_limit"] == 12


def test_operations_self_observer_rejects_unbounded_history():
    app = create_api_app(orchestrator=_FakeOrchestrator(), memory_adapter=_adapter())
    with TestClient(app) as client:
        response = client.get("/operations/self-observer", params={"history_limit": 241})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_graph_store_filters_properties_and_uses_allowlisted_query():
    driver = _FakeDriver([
        {
            "source_element_id": "4:agent",
            "source_labels": ["Agent"],
            "source_properties": {
                "id": "agent:orchestrator-v1",
                "role": "orchestrator",
                "text": "must-not-leak",
            },
            "edge_element_id": "5:edge",
            "relation": "USES_TOOL",
            "edge_properties": {"weight": 0.9, "metadata_json": "must-not-leak"},
            "target_element_id": "4:tool",
            "target_labels": ["Tool"],
            "target_properties": {"name": "sys", "category": "system"},
        }
    ])
    store = GraphStore(neo4j_url="bolt://example", auto_schema=False)
    store._driver = driver

    result = await store.architecture_subgraph(component="orchestrator", limit=12)

    assert result["nodes"][0]["properties"] == {
        "id": "agent:orchestrator-v1",
        "role": "orchestrator",
    }
    assert result["edges"][0]["properties"] == {"weight": 0.9}
    assert "agent:orchestrator-v1" not in driver.query
    assert driver.params["anchor_id"] == "agent:orchestrator-v1"
    assert driver.params["limit"] == 13
    assert "USES_TOOL" in driver.params["relations"]


@pytest.mark.asyncio
async def test_backed_store_architecture_subgraph_uses_configured_timeout(monkeypatch):
    store = BackedMemoryServiceStore.__new__(BackedMemoryServiceStore)
    store.graph_store = _SlowArchitectureGraphStore()
    monkeypatch.setattr(
        "services.config.settings.Settings.MEMORY_ARCHITECTURE_SUBGRAPH_TIMEOUT_SECONDS",
        0.5,
    )

    result = await store.architecture_subgraph(GraphSubgraphRequest(component="memory", limit=12))

    assert result.status.status == "failed"
    assert result.status.error == "architecture_subgraph_timeout"
    assert 450 <= result.query_ms < 1000
