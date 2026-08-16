"""
Service boundary contracts for LIARA.

Defines the request/response interfaces between each component.
This is the API spec for internal service calls.
"""

from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field

from .memory_lifecycle import (
    MemoryEvidence,
    MemoryLifecycleStatus,
    MemoryPromotionActor,
    TrustedPromotionException,
)


# ============================================================================
# API LAYER CONTRACTS (Frontend ↔ API Server)
# ============================================================================

class ChatAttachment(BaseModel):
    """Structured file or rich-content attachment sent alongside a chat message."""

    name: Optional[str] = None
    media_type: Optional[str] = None
    text_content: Optional[str] = None
    # Inline payload is accepted for trusted API/bridge normalization. It is
    # never copied into prompts, history metadata, logs, or model evidence.
    content_base64: Optional[str] = None
    content_url: Optional[str] = None
    size_bytes: Optional[int] = None
    source: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ChatArtifact(BaseModel):
    """Structured assistant artifact (e.g. chart image) returned with chat responses."""

    kind: str
    mime_type: Optional[str] = None
    title: Optional[str] = None
    url: Optional[str] = None
    content_base64: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    source_tool: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExternalToolDefinition(BaseModel):
    """OpenAI-format tool definition forwarded by Continue (or another host)."""
    type: str = "function"
    function: Dict[str, Any] = Field(default_factory=dict)


class ExternalToolCall(BaseModel):
    """A pending tool call that should be executed by the external host (e.g. Continue)."""
    id: str
    type: str = "function"
    function: Dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    """Client sends message to chat endpoint."""
    session_id: str
    user_id: str
    display_name: Optional[str] = None
    message: str
    attachments: List[ChatAttachment] = Field(default_factory=list)
    tools_override: Optional[List[str]] = None
    # OpenAI-format tool definitions provided by the external caller (e.g. Continue).
    available_tools: Optional[List[ExternalToolDefinition]] = None
    # Opt-in switch: only callers that can execute/roundtrip tool calls should enable this.
    allow_external_tool_calls: bool = False
    # Pre-formatted tool results from a previous tool-call turn.
    tool_results: Optional[List[Dict[str, Any]]] = None
    max_tokens: Optional[int] = 2048
    # Optional inference provider preference (e.g. "llama_cpp", "ollama", "ll_ol_fallback", "hybrid").
    preferred_provider: Optional[str] = None
    # Optional model preference forwarded to inference layer.
    preferred_model: Optional[str] = None
    # Optional caller/source tag for policy and risk calibration.
    request_source: Optional[str] = None
    # If true, validator performs a source-aware risk reassessment pass.
    risk_reassessment: bool = False
    sandbox_root: Optional[str] = None
    # Optional user-level quality signal from UI/API in [0,1].
    # If omitted, validator scoring remains purely system-driven.
    user_feedback_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # Optional star feedback from UI/API in [1,6].
    # Only used when user_feedback_score is not provided.
    user_feedback_stars: Optional[int] = Field(default=None, ge=1, le=6)


class ChatResponse(BaseModel):
    """API returns chat response with metadata."""
    run_id: str
    response: str
    tools_used: List[str]
    tool_outputs: Dict[str, Any]
    llm_provider: str
    llm_model: str
    ttft_ms: Optional[float] = None
    gen_ms: Optional[float] = None
    validation_passed: bool
    metadata: Dict[str, Any] = {}
    artifacts: Optional[List[ChatArtifact]] = None
    # Populated when LIARA decides to call an external (Continue-hosted) tool.
    pending_tool_calls: Optional[List[ExternalToolCall]] = None


# ============================================================================
# ORCHESTRATOR CONTRACTS (API ↔ Kernel/Orchestrator)
# ============================================================================

class OrchestratorRequest(BaseModel):
    """Request from API to Orchestrator."""
    session_id: str
    run_id: str
    user_id: str
    display_name: Optional[str] = None
    query: str
    routing_query: Optional[str] = None
    attachments: List[ChatAttachment] = Field(default_factory=list)
    tools_override: Optional[List[str]] = None
    # External tools provided by the caller (e.g. Continue); forwarded for LLM context.
    available_tools: Optional[List["ExternalToolDefinition"]] = None
    # Opt-in switch for external tool-call planning/return path.
    allow_external_tool_calls: bool = False
    # Tool results from a previous external tool-call turn.
    tool_results: Optional[List[Dict[str, Any]]] = None
    max_tokens: Optional[int] = 2048
    # Optional inference provider preference requested by the caller.
    preferred_provider: Optional[str] = None
    # Optional model preference requested by the caller.
    preferred_model: Optional[str] = None
    # Optional caller/source tag for policy and risk calibration.
    request_source: Optional[str] = None
    # If true, validator performs a source-aware risk reassessment pass.
    risk_reassessment: bool = False
    sandbox_root: Optional[str] = None
    # Optional user-level quality signal from UI/API in [0,1].
    user_feedback_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # Optional star feedback from UI/API in [1,6].
    # Only used when user_feedback_score is not provided.
    user_feedback_stars: Optional[int] = Field(default=None, ge=1, le=6)
    simulation_mode: bool = False  # If True, simulate tool execution without real side effects


class OrchestratorResponse(BaseModel):
    """Response from Orchestrator to API."""
    run_id: str
    final_response: str = ""
    tools_executed: List[str] = Field(default_factory=list)
    tool_results: Dict[str, Any] = Field(default_factory=dict)
    state_final: str = "complete"
    llm_generation: Dict[str, Any] = Field(default_factory=dict)
    validation_result: Dict[str, Any] = Field(default_factory=dict)
    session_id: Optional[str] = None
    reasoning_snapshot: Optional[Any] = None
    total_duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    artifacts: Optional[List[ChatArtifact]] = None
    execution_trace: List[Dict[str, Any]] = Field(default_factory=list)
    # Populated when the LLM / orchestrator decides to call an external tool.
    pending_tool_calls: Optional[List["ExternalToolCall"]] = None

    @property
    def response(self) -> str:
        return self.final_response

    @property
    def status(self) -> str:
        return self.state_final

    @property
    def selected_tools(self) -> List[str]:
        return self.tools_executed


class ReasoningMetricsSnapshot(BaseModel):
    """Audit-only reasoning metrics emitted by the orchestrator runtime."""
    model_config = ConfigDict(extra="allow")

    depth: int = 0
    branching_factor_avg: float = 1.0
    memory_items: int = 0
    tool_calls: int = 0
    token_estimate: int = 0
    context_entropy: float = 0.0
    goal_progress: float = 0.0
    policy_risk: float = 0.0
    depth_cost: float = 0.0
    memory_cost: float = 0.0
    tool_cost: float = 0.0
    entropy_cost: float = 0.0
    total_cost: float = 0.0
    reasoning_cost: float = 0.0
    rds_v2: float = 0.0
    uncertainty_risk: float = 0.0
    complexity_risk: float = 0.0
    total_risk: float = 0.0
    risk_total: float = 0.0
    actionable_risk: float = 0.0
    utility: float = 0.0
    should_soft_limit: bool = False
    should_hard_block: bool = False
    rds_mode: Literal["diagnostic"] = "diagnostic"
    mode: Literal["advisory"] = "advisory"
    compute_backend: Literal["python", "julia"] = "python"
    compute_path: Literal["primary", "fallback"] = "primary"
    fallback_reason: Optional[str] = None


# ============================================================================
# MEMORY LAYER CONTRACTS
# ============================================================================

class MemoryWrite(BaseModel):
    """Generic write operation to any memory tier."""
    tier: str  # "session", "persistent", "retrieval", "pattern"
    key: str
    data: Any
    ttl_seconds: Optional[int] = None


class MemoryRead(BaseModel):
    """Generic read operation from any memory tier."""
    tier: str
    key: str
    default: Any = None


class MemoryServiceStatus(BaseModel):
    """Standard status envelope for liara-memory service responses."""

    status: Literal["success", "partial", "failed"]
    backend: Literal["redis", "postgres", "qdrant", "embedding", "memory-service"]
    degraded: bool = False
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryHealthResponse(BaseModel):
    """Health response for liara-memory service readiness and backend state."""

    status: MemoryServiceStatus
    backend_health: Dict[str, Literal["healthy", "degraded", "unavailable"]] = Field(default_factory=dict)
    device: Optional[str] = None
    execution_devices: List[str] = Field(default_factory=list)
    model: Optional[str] = None
    dimensions: Optional[int] = None
    runtime_backend: Optional[str] = None
    effective_max_length: Optional[int] = None
    configured_model_id: Optional[str] = None
    configured_model_dir: Optional[str] = None


class MemoryMessageRecord(BaseModel):
    """Normalized chat history entry returned by memory service."""

    message_id: str
    session_id: str
    run_id: Optional[str] = None
    user_id: Optional[str] = None
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    created_at: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryFactRecord(BaseModel):
    """Persistent fact entry returned by memory service."""

    fact_id: str
    namespace: str
    key: str
    value: Any
    source: Optional[str] = None
    confidence: Optional[float] = None
    status: MemoryLifecycleStatus = MemoryLifecycleStatus.ephemeral
    promotion_reason: Optional[str] = None
    evidence: List[MemoryEvidence] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    created_at: str
    updated_at: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RetrievalDocument(BaseModel):
    """Semantic retrieval hit returned by liara-memory."""

    document_id: str
    content: str
    score: float
    source: Optional[str] = None
    chunk_index: Optional[int] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EmbeddingVector(BaseModel):
    """Embedding payload returned by embedding endpoint."""

    model: str
    dimensions: int
    vector: List[float]
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryHistoryAppendRequest(BaseModel):
    """Append one message to conversation history."""

    session_id: str
    run_id: Optional[str] = None
    user_id: Optional[str] = None
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryHistoryQueryRequest(BaseModel):
    """Query conversation history for a session or run."""

    session_id: str
    run_id: Optional[str] = None
    limit: int = 50
    before_message_id: Optional[str] = None
    include_tool_messages: bool = True


class MemoryHistoryResponse(BaseModel):
    """Response for history append/query operations."""

    items: List[MemoryMessageRecord] = Field(default_factory=list)
    status: MemoryServiceStatus


class MemoryFactUpsertRequest(BaseModel):
    """Create or update a persistent fact."""

    namespace: str
    key: str
    value: Any
    source: Optional[str] = None
    confidence: Optional[float] = None
    status: MemoryLifecycleStatus = MemoryLifecycleStatus.ephemeral
    promotion_reason: Optional[str] = None
    promotion_actor: Optional[MemoryPromotionActor] = None
    policy_exception: Optional[TrustedPromotionException] = None
    evidence: List[MemoryEvidence] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryFactQueryRequest(BaseModel):
    """Lookup facts by namespace, key, or tags."""

    namespace: str
    key: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    limit: int = 20


class MemoryFactResponse(BaseModel):
    """Response for fact read/write operations."""

    items: List[MemoryFactRecord] = Field(default_factory=list)
    status: MemoryServiceStatus


class MemoryRetrievalQueryRequest(BaseModel):
    """Semantic retrieval request against vector memory."""

    query: str
    top_k: int = 5
    session_id: Optional[str] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
    min_score: Optional[float] = None


class MemoryRetrievalUpsertRequest(BaseModel):
    """Write or refresh a retrieval document."""

    document_id: str
    content: str
    source: Optional[str] = None
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryRetrievalResponse(BaseModel):
    """Response for retrieval search/upsert operations."""

    items: List[RetrievalDocument] = Field(default_factory=list)
    status: MemoryServiceStatus


class MemoryEmbeddingRequest(BaseModel):
    """Request embedding generation from liara-memory or embedding worker."""

    input_text: str
    model: Optional[str] = None
    normalize: bool = True
    metadata: Dict[str, Any] = Field(default_factory=dict)


class MemoryEmbeddingResponse(BaseModel):
    """Response carrying an embedding vector."""

    item: Optional[EmbeddingVector] = None
    status: MemoryServiceStatus


class ContextScope(BaseModel):
    """Scope descriptor for Chroma context searches (Fibonacci-Wächter)."""

    session_id: Optional[str] = None
    run_id: Optional[str] = None
    topic_id: Optional[str] = None
    file: Optional[str] = None
    symbol: Optional[str] = None
    turn_index: Optional[int] = None
    time_decay: Optional[float] = None  # 0.0–1.0, lower = older items penalised more


class ContextDocument(BaseModel):
    """A single context hit returned from Chroma."""

    document_id: str
    content: str
    score: float
    scope: Dict[str, Any] = Field(default_factory=dict)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ContextSearchRequest(BaseModel):
    """Scope-filtered semantic search against Chroma context store."""

    query: str
    scope: ContextScope = Field(default_factory=ContextScope)
    top_k: int = 8
    min_score: Optional[float] = None


class ContextUpsertRequest(BaseModel):
    """Write a document into the Chroma context store with scope metadata."""

    document_id: str
    content: str
    embedding: Optional[List[float]] = None
    scope: ContextScope = Field(default_factory=ContextScope)
    memory_tier: Optional[Literal["working", "short_term", "long_term"]] = None
    ttl_seconds: Optional[int] = None
    expires_at: Optional[float] = None
    promotion_state: Optional[Literal["none", "candidate", "promoted", "pinned"]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def effective_metadata(self) -> Dict[str, Any]:
        payload = dict(self.metadata or {})
        if self.memory_tier is not None:
            payload.setdefault("memory_tier", self.memory_tier)
        if self.ttl_seconds is not None:
            payload.setdefault("ttl_seconds", self.ttl_seconds)
        if self.expires_at is not None:
            payload.setdefault("expires_at", self.expires_at)
        if self.promotion_state is not None:
            payload.setdefault("promotion_state", self.promotion_state)
        return payload


class ContextSearchResponse(BaseModel):
    """Response for context search / upsert operations."""

    items: List[ContextDocument] = Field(default_factory=list)
    status: MemoryServiceStatus


class RelationType(str, Enum):
    """Allowlist of permitted relation-type strings for the graph store."""

    # Orchestrator-generated structural relations
    USES_TOOL = "USES_TOOL"
    INFORMS_RESPONSE = "INFORMS_RESPONSE"
    DIRECT_RESPONSE = "DIRECT_RESPONSE"
    # Semantic graph relations
    RELATED = "RELATED"
    DERIVED_FROM = "DERIVED_FROM"
    # Document / knowledge graph relations
    REFERENCES = "REFERENCES"
    DEPENDS_ON = "DEPENDS_ON"
    PRODUCES = "PRODUCES"
    SUMMARIZES = "SUMMARIZES"
    PART_OF = "PART_OF"
    FOLLOWS = "FOLLOWS"
    DESCRIBES = "DESCRIBES"
    SUPPORTS = "SUPPORTS"
    CONTRADICTS = "CONTRADICTS"
    RESOLVES = "RESOLVES"


class RelationEdge(BaseModel):
    """Directed relationship edge for graph-context operations."""

    source: str
    relation: RelationType
    target: str
    weight: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RelationUpsertRequest(BaseModel):
    """Write or update a validated relation into RELATION_STORE (Neo4j)."""

    source: str
    relation: RelationType
    target: str
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    validated: bool = False
    explicit_acceptance: bool = False
    weight: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RelationExpandRequest(BaseModel):
    """Expand related edges from Neo4j for graph-context hydration."""

    session_id: Optional[str] = None
    run_id: Optional[str] = None
    query: Optional[str] = None
    limit: int = 8


class RelationExpandResponse(BaseModel):
    """Response containing relation edges used for graph-context."""

    items: List[RelationEdge] = Field(default_factory=list)
    status: MemoryServiceStatus


class RelationCleanupExpiredRequest(BaseModel):
    """Request payload to cleanup expired ephemeral relation edges."""

    now_ts: Optional[float] = None
    session_id: Optional[str] = None
    run_id: Optional[str] = None
    limit: int = 5000
    judge_decision: Optional[str] = None
    judge_confidence: Optional[float] = None


class RelationCleanupExpiredResponse(BaseModel):
    """Response for relation cleanup operation."""

    removed: int = 0
    status: MemoryServiceStatus


# ============================================================================
# TOOL COORDINATOR CONTRACTS (Orchestrator ↔ Tool Coordinator)
# ============================================================================

class ToolExecutionRequest(BaseModel):
    """Request to execute a tool."""
    tool_name: str
    parameters: Dict[str, Any]
    timeout_seconds: int = 30
    simulation_mode: bool = False  # If True, generate mock result instead of executing


class ToolExecutionResult(BaseModel):
    """Result of tool execution."""
    tool_name: str
    status: str  # "success", "failed", "partial"
    output: Any
    error: Optional[str] = None
    execution_ms: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# INFERENCE GATEWAY CONTRACTS (Orchestrator ↔ Inference Gateway)
# ============================================================================

class TtsGenerationRequest(BaseModel):
    """Request for binary speech generation by the internal TTS service."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    speaker_profile: str = Field(default="neutral-v1", pattern=r"^[a-z0-9][a-z0-9-]{0,63}$")
    max_audio_tokens: int = Field(
        default=100,
        ge=25,
        le=400,
        description="Maximum generated audio tokens per long-form speech segment.",
    )
    seed: Optional[int] = Field(default=None, ge=0, le=2**32 - 1)


class TtsErrorResponse(BaseModel):
    request_id: str
    code: str
    message: str
    retryable: bool = False
    retry_after_seconds: Optional[float] = None


class TtsDevicePlacement(BaseModel):
    transformer: Literal["CPU", "NPU"]
    dvae: Literal["CPU", "NPU"]
    vocos: Literal["CPU"] = "CPU"


class TtsHealthResponse(BaseModel):
    status: Literal["disabled", "unloaded", "loading", "ready", "degraded", "failed"]
    backend: Literal["minicpmo-openvino"] = "minicpmo-openvino"
    mode: Literal["cpu_reference", "mixed_npu_cpu"]
    devices: TtsDevicePlacement
    model_dir: str
    speaker_profile: str
    loaded: bool
    queue_depth: int = 0
    request_count: int = 0
    failure_count: int = 0
    last_error: Optional[str] = None

class InferenceRequest(BaseModel):
    """Request to generate LLM response."""
    prompt: str
    max_tokens: int = 2048
    temperature: float = 0.7
    provider: str = "hybrid"  # "ll"/"llama_cpp", "ol"/"ollama", "openvino", "openvino_npu_helper", "ll_ol_fallback", "hybrid"
    model: Optional[str] = None
    task_type: Optional[str] = None
    expected_fields: Optional[List[str]] = None


class InferenceResult(BaseModel):
    """LLM generation result."""
    content: str
    provider: str
    model: str
    status: str = "success"          # "success" | "failed" | "timeout"
    error: Optional[str] = None       # populated when status != "success"
    ttft_ms: Optional[float] = None
    gen_ms: Optional[float] = None
    load_ms: Optional[float] = None
    winner_provider: Optional[str] = None
    stop_reason: str = "length"
    metadata: Dict[str, Any] = {}


class InferenceNormalizedResponse(BaseModel):
    """Normalized final envelope for API/stream consumers."""

    status: Literal["success", "failed", "timeout"] = "success"
    content: str = ""
    provider: str
    model: str
    error: Optional[str] = None
    ttft_ms: Optional[float] = None
    gen_ms: Optional[float] = None
    load_ms: Optional[float] = None
    winner_provider: Optional[str] = None
    stop_reason: str = "length"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class InferenceStreamChunk(BaseModel):
    """One normalized text delta chunk."""

    seq: int
    text: str
    is_final: bool = False


class InferenceStreamEvent(BaseModel):
    """Normalized stream event envelope."""

    event: Literal["delta", "final", "error", "meta"]
    run_id: Optional[str] = None
    provider: Optional[str] = None
    chunk: Optional[InferenceStreamChunk] = None
    data: Optional[InferenceNormalizedResponse] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


# ============================================================================
# VALIDATOR CONTRACTS (Orchestrator ↔ Validator)
# ============================================================================

class ValidationContext(BaseModel):
    """Context for validation."""
    original_query: str
    response: str
    tools_used: List[str]
    tool_outputs: Dict[str, Any]
    context_mode: str = "NONE"
    context_sources: Dict[str, int] = Field(default_factory=dict)
    context_documents: str = ""
    graph_relations: List[Dict[str, Any]] = Field(default_factory=list)
    # Optional caller/source tag for policy and risk calibration.
    request_source: Optional[str] = None
    # If true, validator performs a source-aware risk reassessment pass.
    risk_reassessment: bool = False
    # Optional human feedback in [0,1]. None means "no user input".
    user_feedback_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # Optional human star rating in [1,6]. None means "no user input".
    user_feedback_stars: Optional[int] = Field(default=None, ge=1, le=6)
    # EvidenceAssertion.to_dict()-shaped items from EvidenceEngine (Issue #8).
    evidence_states: List[Dict[str, Any]] = Field(default_factory=list)


class ValidationSchoolScore(BaseModel):
    """School-grade style validator score tuple."""

    fach: int = Field(ge=1, le=6)
    code: int = Field(ge=1, le=6)
    robustheit: int = Field(ge=1, le=6)
    gesamt: float
    note_text: str
    confidence: float = Field(ge=0.0, le=1.0)


class ValidationResult(BaseModel):
    """Validation result."""
    passed: bool
    decision: Literal["accept", "revise", "warn", "block"] = "accept"
    checks: Dict[str, Literal["pass", "fail", "skip"]] = Field(default_factory=dict)
    issues: List[str] = []
    confidence_score: float
    suggestions: Optional[List[str]] = None
    score: Optional[ValidationSchoolScore] = None
    risk_flags: List[str] = Field(default_factory=list)


# ============================================================================
# GRAPH V2 CONTRACTS (Neo4j extended domain model)
# ============================================================================

class GraphAgentUpsertRequest(BaseModel):
    agent_id: str
    role: str
    version: str = "1.0"


class GraphTaskUpsertRequest(BaseModel):
    task_id: str
    status: str = "running"
    agent_id: Optional[str] = None


class GraphContextUpsertRequest(BaseModel):
    context_id: str
    context_type: str = "session"


class GraphFactUpsertRequest(BaseModel):
    fact_id: str
    text: str
    source: str = "system"
    context_id: Optional[str] = None
    agent_id: Optional[str] = None
    task_id: Optional[str] = None
    embedding_id: Optional[str] = None


class GraphFactLinkRequest(BaseModel):
    fact_a_id: str
    fact_b_id: str
    relation_type: str = "RELATED"


class GraphEmbeddingUpsertRequest(BaseModel):
    embedding_id: str
    vector_ref: str
    dim: int = 0


class GraphSemanticLinkRequest(BaseModel):
    emb_a_id: str
    emb_b_id: str
    score: float


class GraphToolUpsertRequest(BaseModel):
    name: str
    version: str = "1.0"
    category: str = "system"


class GraphContextGraphRequest(BaseModel):
    context_id: str
    limit: int = 20


class GraphNodeResponse(BaseModel):
    ok: bool = True
    data: Dict[str, Any] = Field(default_factory=dict)
    status: MemoryServiceStatus = Field(
        default_factory=lambda: MemoryServiceStatus(status="success", backend="memory-service")
    )


class GraphContextGraphResponse(BaseModel):
    items: List[Dict[str, Any]] = Field(default_factory=list)
    status: MemoryServiceStatus = Field(
        default_factory=lambda: MemoryServiceStatus(status="success", backend="memory-service")
    )


class GraphSubgraphNode(BaseModel):
    """Read-only, property-filtered node exposed to architecture diagnostics."""

    id: str
    label: str
    title: str
    properties: Dict[str, Any] = Field(default_factory=dict)


class GraphSubgraphEdge(BaseModel):
    """Read-only relationship between two diagnostic subgraph nodes."""

    id: str
    source: str
    target: str
    relation: str
    properties: Dict[str, Any] = Field(default_factory=dict)


class GraphSubgraphRequest(BaseModel):
    """Allowlisted architecture component scope; never accepts raw Cypher."""

    component: Literal["orchestrator", "memory"]
    limit: int = Field(default=20, ge=1, le=25)


class GraphSubgraphResponse(BaseModel):
    component: Literal["orchestrator", "memory"]
    nodes: List[GraphSubgraphNode] = Field(default_factory=list)
    edges: List[GraphSubgraphEdge] = Field(default_factory=list)
    truncated: bool = False
    query_ms: int = 0
    status: MemoryServiceStatus = Field(
        default_factory=lambda: MemoryServiceStatus(status="success", backend="memory-service")
    )
