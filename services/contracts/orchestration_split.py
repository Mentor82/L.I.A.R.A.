"""Contracts for the internal orchestrator split (router/planner/executor).

These schemas are additive and do not replace existing public API contracts.
"""

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


class InputMoodProfile(BaseModel):
    """Communication signal inferred from the incoming turn.

    Mood may shape wording, but never permissions, facts, or policy decisions.
    """

    label: Literal["neutral", "playful", "frustrated", "uncertain", "urgent"] = "neutral"
    valence: float = Field(default=0.0, ge=-1.0, le=1.0)
    arousal: float = Field(default=0.2, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    signals: List[str] = Field(default_factory=list)


class InputEnvironmentProfile(BaseModel):
    """Observable runtime surroundings of the message."""

    request_source: str = "unknown"
    session_history_available: bool = False
    workspace_available: bool = False
    simulation_mode: bool = False
    max_tokens: int = 0


class InputResourceBudget(BaseModel):
    """Advisory exploration budget derived before execution."""

    max_reasoning_depth: int = Field(default=2, ge=1, le=8)
    max_branches: int = Field(default=1, ge=1, le=8)
    max_refinement_steps: int = Field(default=2, ge=1, le=5)
    tool_budget: int = Field(default=0, ge=0, le=8)
    budget_basis: str = "fibonacci_guard"


class RetrievalIntent(BaseModel):
    """Inference-produced retrieval plan; evidence for routing, never authorization."""

    kind: Literal["none", "external_retrieval"] = "none"
    requires_external_information: bool = False
    goal: str = ""
    source_hint: Optional[str] = None
    candidate_url: Optional[str] = None
    search_query: Optional[str] = None
    entities: Dict[str, str] = Field(default_factory=dict)
    uncertainties: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    discovery_required: bool = False
    inference_status: Literal["not_run", "success", "failed", "invalid"] = "not_run"


class InputSituationProfile(BaseModel):
    """Typed interpretation of message plus observable environment.

    This is evidence for downstream routing/planning, not an authorization.
    """

    schema_version: str = "liara.input-situation.v1"
    processing_level: Literal["answer", "think", "plan", "act"] = "answer"
    processing_chain: List[Literal["analyze", "think", "answer", "plan", "act"]] = Field(
        default_factory=lambda: ["analyze", "answer"]
    )
    recommended_path: Literal["direct_answer", "think_answer", "plan_answer", "plan_act", "clarify"] = "direct_answer"
    domain: str = "general"
    topics: List[str] = Field(default_factory=list)
    context_dependency: Literal["none", "conversation", "memory", "workspace", "system", "external", "mixed"] = "none"
    external_information_required: bool = False
    retrieval_intent: RetrievalIntent = Field(default_factory=RetrievalIntent)
    complexity: float = Field(default=0.2, ge=0.0, le=1.0)
    ambiguity: float = Field(default=0.1, ge=0.0, le=1.0)
    risk: Literal["low", "medium", "high"] = "low"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    mood: InputMoodProfile = Field(default_factory=InputMoodProfile)
    environment: InputEnvironmentProfile = Field(default_factory=InputEnvironmentProfile)
    resource_budget: InputResourceBudget = Field(default_factory=InputResourceBudget)
    semantic_scores: Dict[str, float] = Field(default_factory=dict)
    signals: List[str] = Field(default_factory=list)


class RouterRequest(BaseModel):
    """Inputs for tool-routing decisions."""

    query: str
    tools_override: Optional[List[str]] = None
    input_profile: Optional[InputSituationProfile] = None


class RouterDecision(BaseModel):
    """Tool-routing output."""

    selected_tools: List[str]
    reason: str = "keyword_heuristic"
    metadata: Dict[str, Any] = {}


class PlannerRequest(BaseModel):
    """Inputs for plan/prompt construction."""

    query: str
    tools_used: List[str]
    tool_outputs: Dict[str, Any]
    conversation_history: str = ""
    context_documents: str = ""  # Chroma scope-filtered context (run-level short-term)
    fact_context: str = ""
    memory_context: str = ""
    relation_context: str = ""
    working_context: str = ""
    evidence_context: str = ""
    primary_context_kind: str = "NONE"
    input_profile: Optional[InputSituationProfile] = None


class PlannerPlan(BaseModel):
    """Output plan payload for execution."""

    prompt: str
    metadata: Dict[str, Any] = {}


class ExecutorRequest(BaseModel):
    """Inputs for execution stage."""

    tool_names: List[str]
    query: str
    session_id: str = ""
    run_id: str = ""
    user_id: str = ""
    sandbox_root: str = ""
    timeout_seconds: int = 30
    routing_metadata: Dict[str, Any] = {}
    simulation_mode: bool = False  # If True, use mock results for all tools


class ExecutorResult(BaseModel):
    """Outputs for execution stage."""

    tool_outputs: Dict[str, Any]
    success_count: int = 0
    failed_count: int = 0
    metadata: Dict[str, Any] = {}
