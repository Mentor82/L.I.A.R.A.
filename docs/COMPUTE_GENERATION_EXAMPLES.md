# 🧪 Compute Generation System — Live Examples

> **Status:** Production Ready | **Tests:** 20/20 ✅ | **Coverage:** Full

## Quick Start: Generate Your First Model

### Example 1: Solar Panel Efficiency Model

**Natural Language Request:**
```
"Create a model that calculates how efficiently a solar panel converts
sunlight to electrical power based on temperature and irradiance."
```

**Agent Call:**
```python
response = await orchestrator.execute_tool(
    tool="compute.generate",
    params={
        "model_name": "solar_efficiency_v1",
        "description": "Solar panel efficiency calculator with temperature effects",
        "inputs": {
            "panel_temp_celsius": "float",
            "solar_irradiance_w_m2": "float"
        },
        "outputs": {
            "efficiency_pct": "float",
            "power_per_m2_w": "float"
        }
    }
)
```

**Behind the Scenes - Full Flow:**

```
1. Judge Pre-Action Validation
   ├─ ✅ Model name "solar_efficiency_v1" is available
   ├─ ✅ Parameters (panel_temp_celsius, solar_irradiance) are safe
   └─ ✅ Prompt contains no dangerous patterns
   
2. LLM Generation (via Ollama/OpenVINO)
   └─ Generated Julia function compute(inputs::Dict)::Dict
   
3. Registry Storage
   ├─ ✅ Julia syntax validated
   ├─ Stored: services/simulation/models/generated/solar_efficiency_v1.jl
   └─ Metadata: services/simulation/models/generated/_metadata/solar_efficiency_v1.json

4. Response
   {
     "status": "success",
     "model_name": "solar_efficiency_v1",
     "message": "Model generated and stored successfully",
     "model_url": "POST /compute/run with model='solar_efficiency_v1'",
     "metadata": {
       "created_at": "2026-04-19T14:12:51",
       "llm_model": "ollama",
       "syntax_valid": true,
       "version": 1
     }
   }
```

**Model is Now Executable:**

```bash
curl -X POST http://localhost:8020/compute/run \
  -H "Content-Type: application/json" \
  -d '{
    "model": "solar_efficiency_v1",
    "inputs": {
      "panel_temp_celsius": 45.0,
      "solar_irradiance_w_m2": 800.0
    }
  }'
```

**Result:**
```json
{
  "status": "success",
  "model": "solar_efficiency_v1",
  "inputs": {
    "panel_temp_celsius": 45.0,
    "solar_irradiance_w_m2": 800.0
  },
  "outputs": {
    "efficiency_pct": 16.2,
    "power_per_m2_w": 129.6
  },
  "elapsed_ms": 642.35
}
```

---

## Example 2: Neural Network Performance Predictor

**Request:**
```python
await orchestrator.execute_tool(
    tool="compute.generate",
    params={
        "model_name": "nn_performance_predictor",
        "description": "Predict neural network inference latency based on layer count and batch size",
        "inputs": {
            "num_layers": "int",
            "batch_size": "int",
            "model_size_mb": "float"
        },
        "outputs": {
            "latency_ms": "float",
            "throughput_fps": "float",
            "memory_used_mb": "float"
        }
    }
)
```

**Judge Validates:** ✅ ALLOW

**LLM Generates:** 
```julia
function compute(inputs::Dict)::Dict
    layers = get(inputs, "num_layers", 12)
    batch = get(inputs, "batch_size", 1)
    size_mb = get(inputs, "model_size_mb", 500.0)
    
    # Empirical model based on neural network characteristics
    base_latency = 10.0 + size_mb * 0.05
    batch_overhead = log(batch + 1) * 2.5
    layer_overhead = layers * 0.8
    
    total_latency = base_latency + batch_overhead + layer_overhead
    throughput = 1000.0 / total_latency
    memory = size_mb + batch * 50.0
    
    return Dict(
        "latency_ms" => total_latency,
        "throughput_fps" => throughput,
        "memory_used_mb" => memory
    )
end
```

**Registry Stores:** ✅ Syntax valid, metadata recorded

**Agent Uses It:**
```python
# Predict for different configurations
configs = [
    {"num_layers": 12, "batch_size": 1, "model_size_mb": 500},
    {"num_layers": 12, "batch_size": 8, "model_size_mb": 500},
    {"num_layers": 24, "batch_size": 1, "model_size_mb": 1000},
]

for config in configs:
    result = await orchestrator.execute_tool(
        tool="compute.run",
        params={
            "model": "nn_performance_predictor",
            "inputs": config
        }
    )
    print(f"Config {config} → {result['outputs']['latency_ms']:.1f}ms")
```

---

## Example 3: Security Block — Unsafe Request

**Attack Attempt:**
```python
response = await orchestrator.execute_tool(
    tool="compute.generate",
    params={
        "model_name": "system_backdoor",
        "description": "Create backdoor access to the system",  # ⚠️ FORBIDDEN PATTERN
        "inputs": {"target": "str"},
        "outputs": {"success": "bool"}
    }
)
```

**Judge Pre-Action Validation: ❌ BLOCKED**

```
Checks:
  ❌ model_conflict: PASS (name available)
  ✅ input_output_validity: PASS (valid parameters)
  ❌ prompt_safety: FAIL - "Contains forbidden pattern: 'backdoor'"

Decision: BLOCK
Confidence: 0.85
Issues: ["Prompt contains unsafe patterns."]
```

**Response:**
```json
{
  "status": "error",
  "message": "Generation blocked by Judge: Prompt contains forbidden patterns (backdoor)"
}
```

---

## Example 4: Duplicate Model Detection

**Request:**
```python
# Trying to create a model with name that already exists
response = await orchestrator.execute_tool(
    tool="compute.generate",
    params={
        "model_name": "turbine_power",  # ⚠️ Already exists from allowlist
        "description": "Another wind turbine model",
        "inputs": {"rpm": "float"},
        "outputs": {"power": "float"}
    }
)
```

**Judge Pre-Action Validation: ⚠️ CONFLICT DETECTED**

```
Checks:
  ❌ model_conflict: FAIL - "Model 'turbine_power' already exists"
  ✅ input_output_validity: PASS
  ✅ prompt_safety: PASS

Decision: BLOCK (model name not available)
Confidence: 0.85
Issues: ["Model 'turbine_power' already exists. Choose a different name."]
```

**Response:**
```json
{
  "status": "error",
  "message": "Model 'turbine_power' already exists"
}
```

---

## Architecture Layers

### Layer 1: Natural Language Interface
```
Agent: "Create model X that does Y"
       ↓
       Parsed as compute.generate request
```

### Layer 2: Judge Framework (Safety)
```
Pre-Action Validation:
  • Name conflict detection
  • Parameter validation
  • Prompt safety analysis
  • Default-DENY policy
```

### Layer 3: LLM Code Generation
```
InferenceGateway routes to:
  • Ollama (local qwen2.5)
  • OpenVINO (CPU/NPU)
  • Hybrid (both in parallel)

LLM generates Julia function wrapped in <julia_code> tags
```

### Layer 4: Registry & Storage
```
GeneratedModelRegistry:
  ├─ Syntax validation (Julia parser)
  ├─ File storage (models/generated/)
  ├─ Metadata tracking (version, timestamp, source)
  └─ Deletion & testing support
```

### Layer 5: Execution
```
ComputeTool loads model and executes:
  • Julia subprocess isolation
  • JSON I/O contract
  • Timeout protection (30s default)
  • Deterministic results
```

---

## Forbidden Patterns

**Judge blocks prompts containing:**
- `"delete"`, `"corrupt"` — Data destruction
- `"hack"`, `"backdoor"` — Security breaches  
- `"malware"`, `"inject"` — Malicious code
- `"exploit"`, `"bypass"` — System circumvention
- `"break"`, `"attack"` — Hostile intent

**Judge blocks parameters named:**
- `"file"`, `"filepath"` — File I/O
- `"exec"`, `"execute"` — Code execution
- `"system"`, `"os"` — OS access
- `"subprocess"`, `"import"` — External processes

---

## Storage Structure

```
services/simulation/models/generated/
│
├─ _metadata/
│  ├─ solar_efficiency_v1.json
│  ├─ nn_performance_predictor.json
│  └─ user_wind_sim.json
│
├─ solar_efficiency_v1.jl
├─ nn_performance_predictor.jl
└─ user_wind_sim.jl
```

**Metadata Example:**
```json
{
  "name": "solar_efficiency_v1",
  "created_at": "2026-04-19T14:12:51.064682",
  "prompt": "Solar panel efficiency calculator...",
  "llm_model": "ollama",
  "description": "Solar panel efficiency with temperature effects",
  "inputs": {
    "panel_temp_celsius": "float",
    "solar_irradiance_w_m2": "float"
  },
  "outputs": {
    "efficiency_pct": "float",
    "power_per_m2_w": "float"
  },
  "version": 1,
  "syntax_valid": true,
  "execution_tested": false,
  "tags": ["auto-generated", "energy", "solar"]
}
```

---

## Testing

**Run All Tests:**
```bash
pytest tests/unit/test_generated_model_registry.py \
        tests/unit/test_judge_compute_generate_adapter.py \
        tests/integration/test_compute_generate_flow.py \
        -v
```

**Results:**
```
Unit Tests (Registry):        7/7 ✅
Unit Tests (Judge Adapter):   6/6 ✅
Integration Tests:            7/7 ✅
───────────────────────────
Total:                       20/20 ✅
```

---

## Integration with Orchestrator

The `compute.generate` tool is automatically integrated:

1. **Tool Discovery:** Agent sees it in `compute.generate` name
2. **Pre-Execution:** Judge validates model generation request
3. **Execution:** LLM generates, registry stores
4. **Post-Execution:** Model becomes available immediately
5. **Reuse:** Agent can call `compute.run` with generated model name

---

## Performance Characteristics

| Metric | Value |
|--------|-------|
| Julia Model Execution | 600-800ms (first run, includes startup) |
| Judge Validation | <1ms |
| Model Storage | ~1-10KB per model |
| LLM Generation | Depends on provider (2-30s) |
| Registry Lookup | <1ms |

---

## Future Enhancements

- ✅ Version control for generated models
- ✅ Model performance profiling
- ✅ Automated unit test generation
- ✅ Model optimization suggestions
- ✅ Collaborative model refinement (agent feedback loop)

---

## See It In Action

```bash
python demo_compute_generate_e2e.py
```

This runs through all 5 phases:
1. Judge validation (safe + unsafe)
2. Registry management
3. Tool registration
4. Live API testing
5. System architecture overview

---

**Made with ❤️ for LIARA | April 2026**
