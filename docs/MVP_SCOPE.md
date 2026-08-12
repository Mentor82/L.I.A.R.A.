# MINIMUM VIABLE V1 SCOPE

Historical planning note:

- This document describes an earlier v1 scope snapshot.
- The current public tool surface has since been reduced and consolidated around `sys`, `orientation`, and `plot_chart`.
- Legacy names such as `web_search` and `current_time` should not be read as the current regular CLI/API contract.

Definition of what MUST be built for v1, what CAN be deferred to v2.

## ✅ V1 REQUIREMENTS (MVP)

### Core Components
- [x] API Server (FastAPI)
  - POST /chat (single turn conversation)
  - GET /history (retrieve past messages)
  - GET /session (current session info)

- [x] Orchestrator (Kernel)
  - State machine (PENDING → COMPLETE)
  - Tool selection heuristic (pick N best tools)
  - Tool execution (sequential)
  - LLM invocation (single provider)

- [x] Tool Infrastructure
  - Base Tool class with contract
  - 3 built-in tools:
    - web_search
    - current_time
    - orientation
  - Registry + discovery

- [x] Memory
  - Session store (Redis)
  - Persistent store (Postgres)
  - Basic RW interface

- [x] Inference
  - Ollama integration (single provider)
  - Request/response translation
  - Basic error handling

- [x] Output Validator
  - Source attribution check
  - Length check
  - Basic consistency check

### Data Model
- Sessions (UUID, user_id, created_at, metadata)
- Runs (UUID, session_id, state, trace)
- Messages (session_id, user_id, message, response, tools_used)
- Tool Executions (run_id, tool_name, output, latency)

### Testing
- Unit tests: Validator, State Manager
- Integration test: One happy path (query → tools → response)
- Fixtures: Mock data generators

### Documentation
- Architecture overview
- Service contracts (with all v1 boundaries)
- Memory design
- API specs
- Quick start guide

## 🚀 V2+ ENHANCEMENTS (Defer)

### Inference
- [ ] Hybrid provider mode (OpenVINO + Ollama racing)
- [ ] Multi-model support (switch by size/latency preference)
- [ ] Speculative decoding
- [ ] Caching + KV cache reuse

### Memory
- [ ] Retrieval tier (Qdrant vectors)
- [ ] Pattern tier (Neo4j graphs)
- [ ] Automatic migration (session → persistent → retrieval)
- [ ] TTL policies + cleanup jobs

### Tools
- [ ] Parallel tool execution
- [ ] Learned tools (from successful patterns)
- [ ] Tool composition (tool chains)
- [ ] Dynamic tool discovery from patterns

### Orchestrator
- [ ] Multi-turn conversation state
- [ ] Reflection/self-correction loop
- [ ] Few-shot learning from successful runs
- [ ] Tool outcome prediction

### Validator
- [ ] Semantic consistency (embedding similarity)
- [ ] Hallucination detection (fact-checking)
- [ ] Tone/style validation
- [ ] User feedback integration

### Observability
- [ ] Distributed tracing (Jaeger/Tempo)
- [ ] Metrics (Prometheus)
- [ ] Logging aggregation (ELK)
- [ ] Dashboard + monitoring

### Scalability
- [ ] Horizontal scaling (Kubernetes)
- [ ] Load balancing (multiple Ollama instances)
- [ ] Worker nodes for tool execution
- [ ] Async job queue (Celery/RabbitMQ)

---

## V1 Success Criteria

✅ **Functional:**
- User asks question → API returns answer with tool sources
- History persists across sessions
- Tools execute successfully
- Responses pass validation

✅ **Performance:**
- End-to-end latency < 5 seconds (95th percentile)
- Tool execution parallelizable (designed, not implemented)
- Memory stores do not block LLM inference

✅ **Reliability:**
- State machine prevents invalid transitions
- Errors don't crash orchestrator (graceful degradation)
- All service boundaries validated with Pydantic

✅ **Maintainability:**
- < 500 lines per module (enforced)
- All public functions have type hints
- Test coverage > 60% (unit + integration)
- Documentation complete for all contracts

