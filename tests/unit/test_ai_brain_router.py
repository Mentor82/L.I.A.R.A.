"""
Unit tests for AI-Brain FastAPI Router Endpoints (services/api/routers/ai_brain.py).
"""

import pytest
from fastapi.testclient import TestClient
from services.api.app import app

client = TestClient(app)


def test_ai_brain_root_discovery():
    r = client.get("/ai-brain/")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "LIARA AI-Brain Gateway"
    assert "capabilities" in data["endpoints"]
    assert "subgraph_bounded" in data["endpoints"]


def test_ai_brain_capabilities():
    r = client.get("/ai-brain/capabilities")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "active"
    assert "USER_CONFIRMED" in data["epistemic_states_supported"]
    assert "Semantic" in data["relation_classes"]


def test_ai_brain_session_authorize_and_bounded_subgraph():
    # 1. Authorize session and obtain Visitor Pass Token
    auth_resp = client.post(
        "/ai-brain/session/authorize",
        json={
            "subject": "test_external_gpt",
            "audience": "ai-brain.liara.mw-dresden.de",
            "max_hops": 2,
            "ttl_seconds": 1800,
        },
    )
    assert auth_resp.status_code == 200
    auth_data = auth_resp.json()
    token = auth_data["visitor_pass"]
    token_id = token["token_id"]

    # 2. Query bounded subgraph using Visitor Pass Token
    subgraph_resp = client.post(
        "/ai-brain/subgraph/bounded",
        json={
            "token_id": token_id,
            "query": "Cortana Nephy Liara",
            "entity_id": "nephy",
            "top_k_seeds": 5,
        },
    )
    assert subgraph_resp.status_code == 200
    sg_data = subgraph_resp.json()
    assert sg_data["status"] == "success"
    assert sg_data["token_id"] == token_id
    assert sg_data["bounded_hops"] == 2
    assert len(sg_data["nodes"]) >= 1


def test_ai_brain_edge_confirmation():
    confirm_resp = client.post(
        "/ai-brain/confirm",
        json={
            "subject": "node_test_a",
            "predicate": "INSPIRED_BY",
            "object": "node_test_b",
        },
    )
    assert confirm_resp.status_code == 200
    data = confirm_resp.json()
    assert data["status"] == "success"
    assert data["epistemic_state"] == "USER_CONFIRMED"
    assert data["confidence"] == 1.0
