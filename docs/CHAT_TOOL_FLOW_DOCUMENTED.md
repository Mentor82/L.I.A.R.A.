"""
Chat Flow with Tool Usage - Complete Documentation

This document describes the complete flow when a user asks the chat interface
to use tools like Python or Julia. It covers streaming, tool selection,
execution, and integration with the LLM response.

=============================================================================
1. OVERVIEW
=============================================================================

When a user sends a message like "Berechne 10 + 20 mit Python", the following
flow is triggered:

    User Input
       ↓
    Chat Request → POST /chat/stream
       ↓
    Orchestrator.run()
       ├─ Tool Selection
       ├─ Tool Execution (via ToolCoordinator)
       ├─ LLM Generation (with tool results)
       ├─ Validation
       └─ Response Assembly
       ↓
    SSE Stream (events)
       ├─ progress: stage=accepted
       ├─ progress: stage=tool_selection
       ├─ progress: stage=tool_execution
       ├─ progress: stage=llm_generation
       ├─ chunk: text fragments
       ├─ final: complete response
       └─ done: stream terminates


=============================================================================
2. STREAM EVENTS - DETAILED
=============================================================================

### Event Type: progress

Emitted at key stages. Structure:

{
  "event": "progress",
  "data": {
    "stage": "tool_selection" | "tool_execution" | "llm_generation" | ...,
    "metadata": {
      "tools_selected": ["python", "julia"],      // For tool_selection stage
      "tools_to_execute": ["python"],             // For tool_execution stage
      "tool_timeout_seconds": 30,
      "parallel": true,
      "context_mode": "MEMORY",                   // For llm_generation
      ...
    }
  }
}

### Event Type: chunk

Text fragments from the LLM response, streamed as they are generated:

{
  "event": "chunk",
  "data": {
    "text": "Das Ergebnis ist "
  }
}
{
  "event": "chunk",
  "data": {
    "text": "30."
  }
}

### Event Type: final

Complete response payload (emitted once, at the end):

{
  "event": "final",
  "data": {
    "run_id": "run-12345",
    "session_id": "session-abc",
    "user_id": "user-xyz",
    "state_final": "complete",
    "final_response": "Das Ergebnis ist 30.",
    "tools_executed": ["python"],
    "tool_results": {
      "python": {
        "status": "success",
        "output": "30",
        "execution_ms": 45,
        "metadata": {...}
      }
    },
    "llm_generation": {
      "provider": "ollama",
      "model": "neural-chat",
      "ttft_ms": 120.5,      // Time to first token
      "gen_ms": 250.3,       // Total generation time
      "context_debug": {
        "mode": "MEMORY",
        "sources": {
          "chroma": 0,
          "qdrant": 1,       // 1 retrieval document used
          "postgres": 3      // 3 facts loaded
        }
      }
    },
    "validation_result": {
      "passed": true,
      "decision": "accept",
      "confidence_score": 0.98
    },
    "execution_trace": [
      {"from": "init", "to": "tool_selection", "duration_ms": 5},
      {"from": "tool_selection", "to": "tool_execution", "duration_ms": 3},
      {"from": "tool_execution", "to": "llm_generation", "duration_ms": 250},
      {"from": "llm_generation", "to": "validation", "duration_ms": 15},
      {"from": "validation", "to": "done", "duration_ms": 2}
    ]
  }
}

### Event Type: done

Marks the end of the stream:

{
  "event": "done"
}


=============================================================================
3. TOOL SELECTION
=============================================================================

### How it Works

The orchestrator uses keyword matching and query heuristics to select tools:

- User query contains "Python" or "Julia" → usually select `sys` with compute-oriented command routing
- User query contains "time" → select `sys`
- User query contains URL/content lookup intent → usually select `sys` with fetch/lookup command routing

### Tool Registry

Available tools are managed by services/tools/registry.py:
- builtin/wsl_executor.py → `sys` tool for system commands, filesystem access, fetch, and compute dispatch
- builtin/orientation.py → orientation tool
- builtin/simulation.py → `compute.run`
- builtin/compute_generate.py → `compute.generate`
- builtin/plot_chart.py → `plot_chart`

### Tool Override

Users can explicitly override tool selection via query parameters or metadata:

POST /chat/stream
{
  "session_id": "...",
  "user_id": "...",
  "message": "Any query",
  "tools_override": ["sys"]  // Use only the canonical public tool path
}


=============================================================================
4. TOOL EXECUTION
=============================================================================

### ToolCoordinator

Located in services/tools/coordinator.py, it:
1. Receives a list of ToolExecutionRequest objects
2. Validates parameters against the tool's schema
3. Executes tools in parallel (async/await, asyncio.gather)
4. Applies per-tool timeout (e.g., 30 seconds)
5. Catches exceptions and wraps them in ToolExecutionResult
6. Returns dict[tool_name] → ToolExecutionResult

### Example: Python Tool

Request:

{
  "tool_name": "python",
  "parameters": {
    "code": "print(2 + 2)"
  },
  "timeout_seconds": 30
}

Execution:
- ToolCoordinator looks up tool class from registry
- Calls python_tool.execute(code="print(2 + 2)")
- Captures stdout/stderr, execution time
- Returns result after 30s timeout or earlier if done

Result:

{
  "tool_name": "python",
  "status": "success",
  "output": "4",
  "execution_ms": 23,
  "error": null
}

### Example: Julia Tool

Request:

{
  "tool_name": "julia",
  "parameters": {
    "code": "println(2 .* [1, 2, 3])"
  },
  "timeout_seconds": 30
}

Result:

{
  "tool_name": "julia",
  "status": "success",
  "output": "2\n4\n6",
  "execution_ms": 1500,  // Slower due to Julia startup
  "error": null
}

### Parallel Execution

When multiple tools are selected, they are executed in parallel:

```python
requests = [
    ToolExecutionRequest(tool_name="python", ...),
    ToolExecutionRequest(tool_name="sys", ...),
    ToolExecutionRequest(tool_name="julia", ...)
]
results = await coordinator.execute_tools_parallel(requests)
# All three run concurrently
```


=============================================================================
5. LLM GENERATION WITH TOOL RESULTS
=============================================================================

### Prompt Assembly

The orchestrator builds a prompt that includes:

```
[SYSTEM]
You are a helpful assistant.

[TOOLS_EXECUTED]
The following tools have been executed:

Tool: python
Result: 
  Code: print(2 + 2)
  Output: 4
  Status: success

Tool: julia
Result:
  Code: println([1, 2, 3] .* 2)
  Output: 2
         4
         6
  Status: success

[CHROMA_CONTEXT]
(Relevant memory/context documents, if any)

[INSTRUCTION]
User query: "Berechne 10 + 20 mit Python und gib das Ergebnis aus."

Generate a response based on the tool results above.
```

### LLM Processing

The prompt is sent to the inference gateway:

```python
inference_request = InferenceRequest(
    prompt=assembled_prompt,
    max_tokens=512,
    temperature=0.7,
    provider="ollama"  # or "openvino", "hybrid"
)
result = await inference_gateway.infer(inference_request)
```

Result includes:
- content: "Das Ergebnis ist 30."
- provider: "ollama"
- model: "neural-chat:7b"
- ttft_ms: 120.5 (time to first token)
- gen_ms: 250.3 (total generation time)
- stop_reason: "stop"


=============================================================================
6. COMPLETE EXAMPLE FLOW
=============================================================================

### User Input

Message: "Berechne den Durchschnitt von [10, 20, 30] mit Python."

### Step 1: Tool Selection

Orchestrator analyzes query:
- "Python" keyword detected → include python tool
- "average/Durchschnitt" keyword → may request math computation

Selected tools: ["python"]

Progress event (tool_selection):
```json
{
  "stage": "tool_selection",
  "metadata": {
    "tools_selected": ["python"],
    "selection_reason": "keyword_match"
  }
}
```

### Step 2: Tool Execution

Execute Python code to compute average:

ToolExecutionRequest:
```json
{
  "tool_name": "python",
  "parameters": {
    "code": "print(sum([10, 20, 30]) / len([10, 20, 30]))"
  },
  "timeout_seconds": 30
}
```

ToolCoordinator executes:
- Validates code parameter exists
- Runs Python interpreter with the code
- Captures output: "20.0"
- Returns result

Progress event (tool_execution):
```json
{
  "stage": "tool_execution",
  "metadata": {
    "tools_to_execute": ["python"],
    "tool_timeout_seconds": 30,
    "parallel": true
  }
}
```

Tool result:
```json
{
  "python": {
    "status": "success",
    "output": "20.0",
    "execution_ms": 45
  }
}
```

### Step 3: LLM Generation

Assembled prompt:
```
[SYSTEM]
You are a helpful assistant. Respond in German.

[TOOLS_EXECUTED]
Tool: python
Code: print(sum([10, 20, 30]) / len([10, 20, 30]))
Output: 20.0
Status: success

[INSTRUCTION]
User query: "Berechne den Durchschnitt von [10, 20, 30] mit Python."

Respond with the result and a brief explanation.
```

LLM generates response:
"Der Durchschnitt von [10, 20, 30] ist 20.0. Das habe ich mit Python berechnet."

Chunks emitted:
```json
{"event": "chunk", "data": {"text": "Der Durchschnitt von [10, 20, 30]"}}
{"event": "chunk", "data": {"text": " ist 20.0."}}
{"event": "chunk", "data": {"text": " Das habe ich mit Python berechnet."}}
```

### Step 4: Validation

Response is validated:
- Does not contain sensitive data
- Matches the query intent
- Tool results are correctly cited

Validation result: passed, confidence_score=0.97

### Step 5: Final Response

Final event emitted:
```json
{
  "event": "final",
  "data": {
    "run_id": "run-abc123",
    "final_response": "Der Durchschnitt von [10, 20, 30] ist 20.0. Das habe ich mit Python berechnet.",
    "tools_executed": ["python"],
    "tool_results": {
      "python": {
        "status": "success",
        "output": "20.0",
        "execution_ms": 45
      }
    },
    "execution_trace": [
      {"from": "init", "to": "tool_selection", "duration_ms": 3},
      {"from": "tool_selection", "to": "tool_execution", "duration_ms": 2},
      {"from": "tool_execution", "to": "llm_generation", "duration_ms": 250},
      {"from": "llm_generation", "to": "validation", "duration_ms": 12},
      {"from": "validation", "to": "done", "duration_ms": 1}
    ]
  }
}
```

Stream ends with "done" event.


=============================================================================
7. ERROR HANDLING
=============================================================================

### Tool Execution Error

If Python tool times out or crashes:

ToolExecutionResult:
```json
{
  "tool_name": "python",
  "status": "failed",
  "output": null,
  "error": "Tool execution timed out after 30s",
  "execution_ms": 30000
}
```

LLM is informed via prompt:
```
[TOOLS_EXECUTED]
Tool: python
Code: <code>
Status: failed
Error: Tool execution timed out after 30s
```

LLM generates fallback response:
"Das Python-Skript konnte nicht ausgeführt werden (Timeout). ..."

### Tool Not Found

If user requests unknown tool:

Tool selection fails → orchestrator may:
1. Continue without the tool (if not critical)
2. Return an error response
3. Suggest available tools

Progress event indicates error.

### Validation Failure

If final response fails validation:

- confidence_score drops
- response_quality: "low"
- Orchestrator may retry or return partial response


=============================================================================
8. TESTING
=============================================================================

Run the documented test suite:

```powershell
# Unit tests (with fakes/mocks)
c:/ai/LIARA/.venv/Scripts/python.exe -m pytest tests/unit/test_tool_coordinator.py -v
c:/ai/LIARA/.venv/Scripts/python.exe -m pytest tests/unit/test_embedding_chat_flow.py -v

# Integration tests (requires running API on port 8010)
$env:RUN_LIVE_CHAT_TOOL_TESTS = "1"
$env:LIARA_API_BASE_URL = "http://127.0.0.1:8010"
c:/ai/LIARA/.venv/Scripts/python.exe -m pytest tests/integration/test_chat_tool_flow_documented.py -v

# Reset env vars afterwards
Remove-Item Env:RUN_LIVE_CHAT_TOOL_TESTS
Remove-Item Env:LIARA_API_BASE_URL
```

```bash
# Bash / WSL equivalent
RUN_LIVE_CHAT_TOOL_TESTS=1 LIARA_API_BASE_URL=http://127.0.0.1:8010 \
  pytest tests/integration/test_chat_tool_flow_documented.py -v
```

### Test Cases

1. **test_chat_stream_with_python_tool_execution()**
   - User requests Python computation
   - Verifies tool selection, execution, and response

2. **test_chat_stream_with_julia_tool_execution()**
   - User requests Julia computation
   - Verifies tool execution and metadata

3. **test_chat_stream_with_multiple_tool_invocations()**
   - Multi-turn chat with tool usage
   - Verifies memory persistence across turns

4. **test_chat_stream_tool_execution_metadata()**
   - Validates rich metadata in progress and final events
   - Checks execution trace completeness


=============================================================================
9. CONFIGURATION
=============================================================================

Environment variables that affect tool behavior:

- TOOL_EXECUTION_TIMEOUT_SECONDS (default: 30)
  Max time allowed for any single tool

- TOOL_PARALLEL_EXECUTION (default: true)
  Whether to run multiple tools concurrently

- PYTHON_TOOL_ENABLED (default: true)
  Enable/disable Python tool

- JULIA_TOOL_ENABLED (default: true)
  Enable/disable Julia tool

- MEMORY_MODE (default: "in-process")
  How to retrieve context: "in-process", "service"

- QDRANT_URL (default: empty)
  If set, enables semantic retrieval via Qdrant


=============================================================================
10. REFERENCES
=============================================================================

- services/orchestrator/orchestrator.py - Main orchestration logic
- services/tools/coordinator.py - Tool execution coordination
- services/tools/builtin/python.py - Python tool implementation
- services/tools/builtin/julia.py - Julia tool implementation
- services/api/app.py - Chat endpoint (/chat/stream)
- services/inference/gateway.py - LLM inference
- tests/integration/test_chat_tool_flow_documented.py - Full test examples
"""
