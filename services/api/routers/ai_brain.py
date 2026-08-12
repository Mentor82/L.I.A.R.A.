import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Query, Header, Body
from pydantic import BaseModel, Field

from ai_brain.auth import pass_auth_manager
from ai_brain.schema import (
    EpistemicState,
    BoundedSubgraphRequest,
    BoundedSubgraphResponse,
    BrainEdge,
    EdgeProvenance,
)
from ai_brain.subgraph_engine import BoundedSubgraphEngine
from ai_brain.builder import BrainBuilder

router = APIRouter(prefix="/ai-brain", tags=["AI-Brain (ADR-007)"])
global_subgraph_engine = BoundedSubgraphEngine()

# Initialize default seed graph into global_subgraph_engine
builder = BrainBuilder(engine=global_subgraph_engine)
builder.build_from_export(
    export_dir="C:\\Users\\WM\\Downloads\\4cebb8d4bf17b0c985c97eebb27ca9295c258fdfa500a1ea6c86991718588496-2026-08-08-18-43-06-4001039de5814348a678fa04ba551d2f",
    entity_id="nephy",
    limit=10,
)


class AuthorizeSessionRequest(BaseModel):
    subject: str = Field(description="External agent session identifier")
    audience: str = Field(default="ai-brain.liara.mw-dresden.de")
    scopes: List[str] = Field(default_factory=lambda: ["facts:read", "relations:read", "projects:read"])
    allowed_epistemic_states: List[EpistemicState] = Field(
        default_factory=lambda: [
            EpistemicState.USER_CONFIRMED,
            EpistemicState.VERIFIED,
            EpistemicState.INFERENCE,
        ]
    )
    max_hops: int = Field(default=2, ge=1, le=5)
    ttl_seconds: int = Field(default=1800)


class AttenuateSessionRequest(BaseModel):
    parent_token_id: str
    sub_subject: str
    sub_scopes: Optional[List[str]] = None
    sub_epistemic_states: Optional[List[EpistemicState]] = None
    sub_max_hops: Optional[int] = None


class EdgeConfirmRequest(BaseModel):
    subject: str
    predicate: str
    object: str


@router.get("/")
def get_ai_brain_root() -> Dict[str, Any]:
    """HATEOAS Self-Describing Discovery Endpoint for External AI Agents."""
    return {
        "service": "LIARA AI-Brain Gateway",
        "purpose": "Controlled semantic-context access for external AI agents",
        "adr_specification": "ADR-007 Epistemic Subgraph & Visitor Pass Capability Paradigm",
        "endpoints": {
            "capabilities": "/ai-brain/capabilities",
            "session_authorize": "/ai-brain/session/authorize",
            "session_attenuate": "/ai-brain/session/attenuate",
            "subgraph_bounded": "/ai-brain/subgraph/bounded",
            "confirm_edge": "/ai-brain/confirm",
        },
    }


@router.get("/capabilities")
def get_ai_brain_capabilities() -> Dict[str, Any]:
    """Return capability descriptor for external AI agents."""
    return {
        "status": "active",
        "epistemic_states_supported": [e.value for e in EpistemicState],
        "relation_classes": [
            "Semantic",
            "Evolution",
            "System",
            "PersonalDenkraum",
        ],
        "default_max_hops": 2,
        "max_hops_limit": 5,
        "security_invariants": [
            "Authorization constrains retrieval, not merely presentation.",
            "Capabilities may attenuate, never amplify.",
        ],
    }


@router.post("/session/authorize")
def authorize_session(payload: AuthorizeSessionRequest) -> Dict[str, Any]:
    """Issue a new Visitor Pass Token with subject/audience binding."""
    token = pass_auth_manager.issue_pass(
        subject=payload.subject,
        audience=payload.audience,
        scopes=payload.scopes,
        allowed_epistemic_states=payload.allowed_epistemic_states,
        max_hops=payload.max_hops,
        ttl_seconds=payload.ttl_seconds,
    )
    return {
        "status": "success",
        "visitor_pass": token.model_dump(),
    }


@router.post("/session/attenuate")
def attenuate_session(payload: AttenuateSessionRequest) -> Dict[str, Any]:
    """
    Derive an attenuated sub-token.
    Enforces Invariant 2: Capabilities may attenuate, never amplify.
    """
    try:
        sub_token = pass_auth_manager.attenuate_pass(
            parent_token_id=payload.parent_token_id,
            sub_subject=payload.sub_subject,
            sub_scopes=payload.sub_scopes,
            sub_epistemic_states=payload.sub_epistemic_states,
            sub_max_hops=payload.sub_max_hops,
        )
        return {
            "status": "success",
            "attenuated_visitor_pass": sub_token.model_dump(),
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/subgraph/bounded", response_model=BoundedSubgraphResponse)
def query_bounded_subgraph(request: BoundedSubgraphRequest) -> BoundedSubgraphResponse:
    """
    Query Bounded Semantic Subgraph.
    Enforces Invariant 1: Traversal-Level Authorization.
    """
    try:
        return global_subgraph_engine.query_bounded_subgraph(request)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/confirm")
def confirm_edge(payload: EdgeConfirmRequest) -> Dict[str, Any]:
    """Confirm an AI-inferred edge as USER_CONFIRMED (confidence = 1.0)."""
    edge_id = f"edge_confirmed_{payload.subject}_{payload.predicate}_{payload.object}"
    edge = BrainEdge(
        id=edge_id,
        subject_id=payload.subject,
        predicate=payload.predicate,
        object_id=payload.object,
        epistemic_state=EpistemicState.USER_CONFIRMED,
        provenance=EdgeProvenance(
            source_type="user_confirmed",
            confidence=1.0,
            verified=True,
            scope="projects:read",
        ),
    )
    global_subgraph_engine.graph_store.upsert_edge(edge)
    return {
        "status": "success",
        "confirmed_edge_id": edge_id,
        "epistemic_state": EpistemicState.USER_CONFIRMED.value,
        "confidence": 1.0,
    }
