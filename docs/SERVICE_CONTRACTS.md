# SERVICE CONTRACTS

Exact request/response schemas for each service boundary.

## API → Orchestrator

### Request: ChatRequest

```python
class ChatRequest(BaseModel):
    session_id: str
    user_id: str
    message: str
    attachments: List[ChatAttachment] = []
    tools_override: Optional[List[str]] = None
    available_tools: Optional[List[ExternalToolDefinition]] = None
    allow_external_tool_calls: bool = False
    tool_results: Optional[List[Dict[str, Any]]] = None
    max_tokens: Optional[int] = 2048
    preferred_provider: Optional[str] = None
    preferred_model: Optional[str] = None
    sandbox_root: Optional[str] = None
    user_feedback_score: Optional[float] = None
    user_feedback_stars: Optional[int] = None
```

`ChatAttachment` carries file metadata plus optional `text_content` or `content_url`.
Each attachment can receive `metadata.scan` at API ingress with the scanner verdict.

### Response: ChatResponse

```python
class ChatResponse(BaseModel):
    run_id: str
    response: str
    tools_used: List[str]
    tool_outputs: Dict[str, Any]
    llm_provider: str
    llm_model: str
    ttft_ms: Optional[float] = None
    gen_ms: Optional[float] = None
    validation_passed: bool
    metadata: Dict[str, Any]
```

`ChatResponse.metadata` includes `attachments` and `attachment_scan_results`
so clients can inspect which files were accepted and how they were scanned.

---

## Orchestrator → Tool Coordinator

### Request: ToolExecutionRequest

```python
class ToolExecutionRequest(BaseModel):
    tool_name: str
    parameters: Dict[str, Any]
    timeout_seconds: int = 30
```

### Response: ToolExecutionResult

```python
class ToolExecutionResult(BaseModel):
    tool_name: str
    status: str  # "success", "failed", "partial"
    output: Any
    error: Optional[str] = None
    execution_ms: Optional[float] = None
```

---

## Orchestrator → Inference Gateway

### Request: InferenceRequest

```python
class InferenceRequest(BaseModel):
    prompt: str
    max_tokens: int = 2048
    temperature: float = 0.7
    provider: str = "hybrid"  # "ll"/"llama_cpp", "ol"/"ollama", "openvino", "ll_ol_fallback", "hybrid"
    model: Optional[str] = None
```

### Response: InferenceResult

```python
class InferenceResult(BaseModel):
    content: str
    provider: str
    model: str
    ttft_ms: Optional[float] = None
    gen_ms: Optional[float] = None
    load_ms: Optional[float] = None
    winner_provider: Optional[str] = None
    stop_reason: str = "length"
    metadata: Dict[str, Any]
```

---

## Orchestrator → Validator

### Request: ValidationContext

```python
class ValidationContext(BaseModel):
    original_query: str
    response: str
    tools_used: List[str]
    tool_outputs: Dict[str, Any]
```

### Response: ValidationResult

```python
class ValidationResult(BaseModel):
    passed: bool
    issues: List[str] = []
    confidence_score: float  # 0.0-1.0
    suggestions: Optional[List[str]] = None
```

---

## State Machine: Run Lifecycle

```text
PENDING
  ↓
TOOL_SELECTION
  ↓
TOOL_EXECUTION
  ↓
LLM_GENERATION
  ↓
VALIDATION
  ↓
COMPLETE
```

All transitions are tracked with timestamp + metadata.

---

## Memory → Embedding Service

### Request: MemoryEmbeddingRequest

```python
class MemoryEmbeddingRequest(BaseModel):
    input_text: str
    model: Optional[str] = None    # falls back to service default
    normalize: bool = True
    metadata: Dict[str, Any] = {}
```

### Response: MemoryEmbeddingResponse

```python
class MemoryEmbeddingResponse(BaseModel):
    item: Optional[EmbeddingVector]       # None on failure
    status: MemoryServiceStatus

class EmbeddingVector(BaseModel):
    model: str
    dimensions: int
    vector: List[float]
    metadata: Dict[str, Any] = {}
```

Endpoints:

- `POST {EMBEDDING_SERVICE_BASE_URL}/embedding/generate`
- `GET  {EMBEDDING_SERVICE_BASE_URL}/health`

Failure behavior: `BackedMemoryServiceStore.generate_embedding()` returns
`status="failed"` with `degraded=True` when the service is unreachable.
The memory service health reflects the embedding backend state under the
`"embedding"` key, but only downgrades overall status when
`EMBEDDING_SERVICE_BASE_URL` is explicitly configured.
