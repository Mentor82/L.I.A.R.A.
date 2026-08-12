"""
ADR-007 Schema Definitions for AI-Brain.
Defines Epistemic States, 4-Class Relations, Provenance Metadata, and Visitor Pass Capabilities.
"""

from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import time


class EpistemicState(str, Enum):
    USER_CONFIRMED = "USER_CONFIRMED"  # Statement explicitly confirmed by authoritative user (confidence = 1.0)
    VERIFIED = "VERIFIED"              # Proven relation backed by empirical evidence or test
    INFERENCE = "INFERENCE"            # Algorithmic AI inference (remains marked as inference)
    HYPOTHESIS = "HYPOTHESIS"          # Exploratory or speculative statement
    CONTRADICTED = "CONTRADICTED"      # Delivered with explicit counter-evidence
    SUPERSEDED = "SUPERSEDED"          # Historically visible, marked non-current state


class SemanticRelation(str, Enum):
    RELATES_TO = "RELATES_TO"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    REFINES = "REFINES"
    EXPLAINS = "EXPLAINS"
    DERIVED_FROM = "DERIVED_FROM"
    SIMILAR_TO = "SIMILAR_TO"


class EvolutionRelation(str, Enum):
    PRECEDES = "PRECEDES"
    EVOLVED_INTO = "EVOLVED_INTO"
    SUPERSEDES = "SUPERSEDES"
    REVISITS = "REVISITS"
    CURRENT_VERSION_OF = "CURRENT_VERSION_OF"


class SystemRelation(str, Enum):
    PART_OF = "PART_OF"
    DEPENDS_ON = "DEPENDS_ON"
    IMPLEMENTS = "IMPLEMENTS"
    GOVERNS = "GOVERNS"
    VALIDATES = "VALIDATES"
    USES = "USES"
    PRODUCED_BY = "PRODUCED_BY"


class PersonalDenkraumRelation(str, Enum):
    INSPIRED_BY = "INSPIRED_BY"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    PREFERENCE_FOR = "PREFERENCE_FOR"
    GOAL_OF = "GOAL_OF"
    DECISION_ABOUT = "DECISION_ABOUT"
    OPEN_QUESTION = "OPEN_QUESTION"
    IDEA_FOR = "IDEA_FOR"


class EdgeProvenance(BaseModel):
    source_type: str = Field(default="conversation", description="conversation | inference | user_confirmed | system")
    source_id: Optional[str] = Field(default=None, description="Thread, message, or commit ID")
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)
    valid_from: Optional[str] = Field(default=None)
    visibility: str = Field(default="shared", description="shared | private")
    scope: str = Field(default="projects:read")
    verified: bool = Field(default=False)


class BrainNode(BaseModel):
    id: str
    entity_id: str = Field(default="nephy", description="Associated AI or user entity ID")
    label: str = Field(default="Concept", description="Concept | Fact | Turn | Thread | Project")
    title: str
    description: Optional[str] = None
    visibility: str = Field(default="shared")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class BrainEdge(BaseModel):
    id: str
    subject_id: str
    predicate: str
    object_id: str
    epistemic_state: EpistemicState = Field(default=EpistemicState.INFERENCE)
    provenance: EdgeProvenance = Field(default_factory=EdgeProvenance)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class VisitorPassToken(BaseModel):
    token_id: str
    subject: str = Field(description="Authorized subject/agent session")
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
    visibility: str = Field(default="shared")
    ttl_seconds: int = Field(default=1800)
    created_at: float = Field(default_factory=time.time)

    def is_expired(self) -> bool:
        return (time.time() - self.created_at) > self.ttl_seconds


class BoundedSubgraphRequest(BaseModel):
    token_id: str
    query: str
    entity_id: str = Field(default="nephy")
    top_k_seeds: int = Field(default=5, ge=1, le=20)
    requested_max_hops: Optional[int] = None


class BoundedSubgraphResponse(BaseModel):
    status: str = "success"
    entity_id: str
    query: str
    seed_nodes: List[str]
    nodes: List[BrainNode]
    edges: List[BrainEdge]
    epistemic_breakdown: Dict[str, int]
    token_id: str
    bounded_hops: int
