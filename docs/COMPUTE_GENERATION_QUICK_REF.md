# Compute Generation Quick Reference

## One-Liner: Generate a Model

```python
# Agent requests a model
result = await agent.call_tool(
    "compute.generate",
    model_name="my_model",
    description="What you want computed",
    inputs={"param1": "float", "param2": "int"},
    outputs={"result": "float"}
)
# Returns: {"status": "success", "model_name": "my_model"}

# Agent uses the generated model
result = await agent.call_tool(
    "compute.run",
    model="my_model",
    inputs={"param1": 3.14, "param2": 42}
)
# Returns: {"status": "success", "outputs": {"result": 123.45}}
```

---

## API Endpoints

### POST /compute/generate
**Generate a Julia model from natural language**

Request:
```json
{
  "model_name": "wind_power_estimator",
  "description": "Estimate wind turbine power output",
  "inputs": {"wind_speed_ms": "float", "blade_radius_m": "float"},
  "outputs": {"power_kw": "float"},
  "llm_provider": "ollama"
}
```

Response (Success):
```json
{
  "status": "success",
  "model_name": "wind_power_estimator",
  "message": "Model generated and stored successfully",
  "metadata": {
    "created_at": "2026-04-19T14:12:51",
    "syntax_valid": true,
    "version": 1
  }
}
```

Response (Error):
```json
{
  "status": "error",
  "message": "Model 'wind_power_estimator' already exists"
}
```

### POST /compute/run
**Execute any model (built-in or generated)**

Request:
```json
{
  "model": "wind_power_estimator",
  "inputs": {"wind_speed_ms": 12.5, "blade_radius_m": 45.0}
}
```

Response:
```json
{
  "status": "success",
  "model": "wind_power_estimator",
  "outputs": {"power_kw": 2847.3},
  "elapsed_ms": 723.45
}
```

### GET /compute/models
**List available allowlisted models**

Response:
```json
{
  "models": [
    {
      "name": "turbine_power",
      "path": "/path/to/turbine_power.jl",
      "present": true
    }
  ]
}
```

---

## Judge Safety Validation

### What Gets Blocked ❌

| Category | Blocks | Allows |
|----------|--------|--------|
| Names | Duplicates, starting with `_` | Unique, alphanumeric + underscore |
| Parameters | `file`, `exec`, `os`, `subprocess` | `speed`, `temp`, `efficiency` |
| Prompts | `hack`, `backdoor`, `malware`, `exploit` | `calculate`, `simulate`, `predict` |

### Examples

**BLOCKED:**
```python
# Duplicate name
{"model_name": "turbine_power"}  # ← Already exists

# Dangerous parameters
{"inputs": {"file_path": "str"}}  # ← Contains "file"

# Unsafe prompt
{"description": "Create backdoor to hack the system"}  # ← "backdoor" forbidden
```

**ALLOWED:**
```python
# New name
{"model_name": "wind_efficiency_analyzer"}  # ✅

# Safe parameters
{"inputs": {"wind_speed_ms": "float"}}  # ✅

# Legitimate prompt
{"description": "Calculate wind turbine efficiency"}  # ✅
```

---

## Generated Model Storage

```
services/simulation/models/generated/
├─ model_name.jl              ← Julia code
├─ model_name.jl              ← Julia code
└─ _metadata/
   ├─ model_name.json         ← Metadata
   └─ model_name.json         ← Metadata
```

**Accessing Generated Models:**
```python
# Via API
POST /compute/run with model="your_generated_model"

# Via Python
from services.simulation.registry import GeneratedModelRegistry
registry = GeneratedModelRegistry()
models = registry.list_models()
code, metadata = registry.load_model("your_model")
```

---

## Registration & Discovery

```python
# Tool is automatically registered
from services.tools.registry import _global_registry

tools = _global_registry.list_tools()
# ["sys", "orientation", "compute.run", "compute.generate"]

# Get tool metadata
metadata = _global_registry.get_metadata("compute.generate")
# {
#   "name": "compute.generate",
#   "description": "Generate a custom Julia computation model...",
#   "required_parameters": ["model_name", "description", "inputs", "outputs"]
# }
```

---

## Judge Integration

```python
# Pre-action validation happens automatically
from services.judge.engine import JudgeEngine
from services.judge.contracts import JudgeContext, JudgeStage

engine = JudgeEngine()

context = JudgeContext(
    request_id="req_001",
    stage=JudgeStage.PRE_ACTION,
    actor="agent",
    intent="generate_model",
    action="compute.generate",
    input={
        "model_name": "my_model",
        "description": "Do something safe",
        "inputs": {"x": "float"},
        "outputs": {"y": "float"}
    }
)

decision = engine.evaluate_pre_action(context)
# decision.decision == JudgeDecisionType.ALLOW
# decision.confidence == 0.95
# decision.checks == [JudgeCheckResult(...), ...]
```

---

## Testing

```bash
# Unit tests (registry + adapter)
pytest tests/unit/test_generated_model_registry.py -v
pytest tests/unit/test_judge_compute_generate_adapter.py -v

# Integration tests
pytest tests/integration/test_compute_generate_flow.py -v

# All together
pytest tests/unit/test_generated_model_registry.py \
        tests/unit/test_judge_compute_generate_adapter.py \
        tests/integration/test_compute_generate_flow.py \
        -v

# Live demo
python demo_compute_generate_e2e.py
```

---

## Common Workflows

### Workflow 1: Single Model Generation & Use

```python
# 1. Generate
gen_result = await agent.call_tool(
    "compute.generate",
    model_name="efficiency_calc",
    description="Calculate system efficiency",
    inputs={"input_power": "float", "output_power": "float"},
    outputs={"efficiency_pct": "float"}
)

# 2. Use immediately
exec_result = await agent.call_tool(
    "compute.run",
    model="efficiency_calc",
    inputs={"input_power": 100.0, "output_power": 85.0}
)
# Returns efficiency_pct = 85.0
```

### Workflow 2: Batch Model Comparison

```python
# Generate multiple models with different approaches
models = []
for approach in ["linear", "polynomial", "logarithmic"]:
    result = await agent.call_tool(
        "compute.generate",
        model_name=f"predictor_{approach}",
        description=f"Predict output using {approach} model"
        inputs={"x": "float"},
        outputs={"y": "float"}
    )
    models.append(result["model_name"])

# Compare on same dataset
for model in models:
    for x_val in [1, 10, 100]:
        result = await agent.call_tool(
            "compute.run",
            model=model,
            inputs={"x": float(x_val)}
        )
        print(f"{model}(x={x_val}) = {result['outputs']['y']}")
```

### Workflow 3: Iterative Refinement

```python
# Generate initial model
v1 = await agent.call_tool("compute.generate", ...)

# Test it
test_result = await agent.call_tool("compute.run", model=v1_name, ...)

# If not good enough, generate improved version
v2 = await agent.call_tool(
    "compute.generate",
    model_name="improved_model",
    description="Better version: " + feedback,
    ...
)
```

---

## Performance Tips

✅ **Good:**
- Keep Julia models focused (single well-defined computation)
- Use simple math operations (faster than complex logic)
- Test with `compute.run` to profile execution time
- Store results in agent memory to avoid recomputation

❌ **Avoid:**
- Complex nested loops (inefficient in Julia)
- File I/O (blocked by safety)
- External network calls (blocked by isolation)
- Very large arrays (memory limited)

---

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| Model generation fails | LLM not responding | Check `llm_provider`, ensure Ollama running |
| "Model already exists" | Name conflict | Choose different `model_name` |
| Generation blocked by Judge | Unsafe prompt | Check for forbidden patterns (hack, backdoor, etc.) |
| Syntax error on execution | Generated code invalid | LLM may have created malformed Julia |
| Model not found | Wrong model name | Use `GET /compute/models` to list available |

---

## Architecture at a Glance

```
Request
  ↓
Judge (validates)
  ↓
LLM generates Julia code
  ↓
Registry stores + validates syntax
  ↓
Model available for compute.run
  ↓
Results returned
```

**Keys:**
- 🔐 Judge blocks unsafe requests
- 📦 Models versioned + stored
- ⚡ Julia subprocess isolated
- 🔄 Reusable across agent calls

---

## Links

- **Full Examples:** [docs/COMPUTE_GENERATION_EXAMPLES.md](COMPUTE_GENERATION_EXAMPLES.md)
- **Architecture:** [docs/ARCHITECTURE.md](ARCHITECTURE.md)
- **Judge Framework:** [docs/judge.md](judge.md)
- **Tests:** `tests/unit/test_generated_model_registry.py`
- **Source:** `services/tools/builtin/compute_generate.py`

---

**Last Updated:** April 19, 2026 | **Status:** Production Ready ✅
