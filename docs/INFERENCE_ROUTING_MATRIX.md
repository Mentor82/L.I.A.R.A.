# LIARA Inference Routing Matrix

Status: Runtime-aligned baseline (implemented + target-policy split)
Date: 2026-04-26

This document separates:

- implemented behavior in current runtime code
- target policy not yet hard-enforced in code

Policy direction for next iterations:

- NPU inference is an assist path for main inference, not a replacement for the main path.
- NPU is used to offload split subtasks when it is possible and useful (latency/throughput gain).

## 1) Runtime Truth (Implemented)

Current provider modes accepted by `InferenceGateway`:

- `llama_cpp`
- `llama_cpp_auto`
- `ollama`
- `ollama_gpu`
- `ollama_cpu`
- `openvino`
- `openvino_npu_helper` (routes to standalone `:8040/infer/helper`)
- `ll_ol_fallback`
- `hybrid`

Default provider:

- `DEFAULT_LLM_PROVIDER` defaults to `ll_ol_fallback`.

Current fallback behavior:

- `ll_ol_fallback` means `llama_cpp` first, then `ollama_gpu`, then `ollama_cpu`.
- `hybrid` runs `ollama` and `openvino` concurrently and returns the first success.

Current startup behavior:

- API startup attempts to launch `llama-server` for the llama-first path.
- If llama startup times out, requests continue with ollama fallback available.

Current request-level override behavior:

- API forwards `preferred_provider` unchanged to orchestrator.
- Bridge can set preferred provider via `BRIDGE_PREFERRED_PROVIDER`.
- exception: when `request_source == co_worker` and provider lock is enabled, orchestrator enforces `CO_WORKER_MAIN_PROVIDER` (default `llama_cpp`) and ignores explicit provider override.

Current invocation behavior:

- direct invoker is active by default.
- queue-ready invoker exists and can fall back to direct mode.
- standalone OpenVINO NPU instance is available via `services.inference.openvino_npu_app:app` (default port `8040`) for isolated helper/offload execution.
- gateway can route explicit helper calls via provider `openvino_npu_helper` (aliases: `ov_npu_helper`, `openvino_helper`).
- orchestrator performs explicit helper task-splitting for short prompts (`intent_classification`, `quick_extract`, `rewrite_fragments`) and falls back to main provider on helper failure.
- co-worker source is pinned to main path (no helper auto-offload).

## 2) What Is Not Yet Enforced

The following are policy intentions, but not fully enforced today:

- (no remaining hard-enforcement gaps in current matrix baseline)

## 3) Implemented Routing Matrix (As-Is)

| Input Condition | Effective Route | Enforced Today | Notes |
| --- | --- | --- | --- |
| `preferred_provider` provided by caller | explicit provider path | yes | caller override wins (`llama_cpp`, `ollama`, `ollama_gpu`, `ollama_cpu`, `openvino`, `ll_ol_fallback`, `hybrid`, `llama_cpp_auto`) |
| `preferred_provider=openvino_npu_helper` | gateway -> standalone helper endpoint | yes | calls `POST http://127.0.0.1:8040/infer/helper` (base URL configurable via `OPENVINO_NPU_BASE_URL`) |
| no explicit provider + short parallelizable helper task | orchestrator classifies helper subtask and routes to `openvino_npu_helper`, then fallback to main path on failure | yes | classifier emits `helper_task_type` + `helper_expected_fields` and supports `intent_classification`, `quick_extract`, `rewrite_fragments` |
| no explicit provider and default settings | `ll_ol_fallback` | yes | llama first, then `ollama_gpu`, then `ollama_cpu` |
| provider=`hybrid` | race: ollama vs openvino | yes | first successful response wins |
| bridge request without explicit provider | default provider path | yes | bridge sets request source, not a hard inference class |
| co_worker request source | forced `CO_WORKER_MAIN_PROVIDER` (default `llama_cpp`) | yes | helper auto-offload is disabled and explicit provider override is ignored while lock is enabled |

## 4) Target Policy Matrix (Planned)

| Target Route Class | Match Criteria | Primary | Fallback 1 | Fallback 2 |
| --- | --- | --- | --- | --- |
| Agent Core | high-context reasoning | `llama_primary` | `ollama_gpu` | `ollama_cpu` |
| Co-Worker | `request_source == co_worker` | `llama_primary` | `ollama_gpu` | `ollama_cpu` |
| NPU Helper Offload | split subtask is short, parallelizable, and non-critical for final reasoning coherence | `openvino_npu_fp16` (helper call) | `llama_primary` | `ollama_gpu` |
| Embedding | embedding generation | `embedding_service_current` | N/A | N/A |

Main-path rule:

- final response synthesis remains on main inference path (`llama_primary` + fallback chain).
- NPU helper calls provide partial artifacts/signals that the main path consumes.

## 5) Timeouts and Failure Handling

Configured timeouts (environment-backed):

- `LLAMA_CPP_TIMEOUT_SECONDS` (default 240)
- `OLLAMA_TIMEOUT_SECONDS` (default follows llama timeout)
- `INFERENCE_BREAKER_ENABLED` (default true)
- `INFERENCE_BREAKER_FAILURE_THRESHOLD` (default 3)
- `INFERENCE_BREAKER_COOLDOWN_SECONDS` (default 90)

Current state:

- timeout handling is per-provider call behavior
- per-backend circuit breaker is implemented in inference gateway with cooldown and half-open probe
- helper-offload telemetry is emitted in routing metadata (`helper_offload_used`, `helper_schema_ok`, `helper_fallback_triggered`).
- inference result metadata includes breaker snapshot under `metadata.breaker` (`state`, `consecutive_failures`, `cooldown_remaining_seconds`, `half_open_probe_in_flight`).
- standardized routing telemetry fields are emitted for each inference call in `context_debug.routing`: `routing_class`, `fallback_depth`, `breaker_state`.

Target extension:

- add backend-level breaker counters, cooldown and half-open probe

## 6) Health and Startup Dependencies

Operational prerequisites around inference path:

1. memory reachable
2. embedding reachable
3. API startup
4. llama-server startup attempt for llama-first route
5. bridge after API

WSL note:

- sandbox-related WSL readiness checks are handled by service guard preflight when WSL mode is enabled.

## 7) Minimal Runtime Decision Pseudocode (Current)

```text
if request.preferred_provider is set:
    provider = request.preferred_provider
else:
    provider = DEFAULT_LLM_PROVIDER  # default: ll_ol_fallback

if provider == ll_ol_fallback:
    try llama_cpp
    if fail: try ollama
elif provider == hybrid:
    run ollama and openvino concurrently
    return first success
else:
    call selected provider directly
```

## 8) Next Enforcement Steps

1. Split ollama fallback into logical GPU/CPU tiers with distinct health/metrics.
2. Add task splitter and NPU helper-offload classifier for short/parallelizable subtasks.
3. Implement per-backend circuit breaker with cooldown + probe.
4. Emit standardized routing telemetry fields on every inference call, including helper-offload counters.

## 9) Acceptance Criteria (For Target Policy)

- co-worker requests do not choose openvino as primary.
- default agent path stays llama-first in healthy state.
- NPU is used as helper/offload only and does not replace main final synthesis.
- fallback path activation is visible in structured metadata.
- no request blocks indefinitely beyond configured timeout chain.
- focused live regressions and smoke checks pass in small batches.
