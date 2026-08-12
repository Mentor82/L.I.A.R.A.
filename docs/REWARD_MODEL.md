# Reward Model for Risk Classification

Status: implemented in trainer + judge + orchestrator, current as of 2026-04-19

## Overview

The Reward Model is a learned classifier trained on safe/unsafe action patterns to augment policy-based judge decisions with probabilistic risk scores. It enables LIARA to:

1. **Predict Safety**: Classify actions and responses as safe (eval_binary=1) or unsafe (eval_binary=0)
2. **Score Risk**: Provide confidence-weighted risk scores (0.0=safe, 1.0=unsafe)
3. **Explain Decisions**: Identify which features/patterns contribute to risk predictions
4. **Augment Judge**: Boost or reduce confidence in judge decisions based on learned patterns

## Architecture

```text
┌─────────────────────────────────────────────────────────┐
│ Reward Model Pipeline                                    │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  1. RiskDatasetGenerator                               │
│     ├─ Extract patterns from Judge logic               │
│     ├─ Generate training samples (safe/unsafe)         │
│     └─ Create eval_binary labels (0=unsafe, 1=safe)   │
│                                                          │
│  2. RewardModel (TF-IDF + LogisticRegression)          │
│     ├─ Feature extraction: commands, intents, calls    │
│     ├─ Binary classification: safe/unsafe              │
│     └─ Output: eval_binary + confidence + risk_score   │
│                                                          │
│  3. RewardModelScorer (Integration Layer)              │
│     ├─ Score actions (pre-action)                      │
│     ├─ Score responses (post-action)                   │
│     └─ Explain predictions                             │
│                                                          │
│  4. Judge Adapters (Policy Augmentation)               │
│     ├─ PreActionAdapter: score input → allow/block     │
│     └─ PostActionAdapter: score output → validate      │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

## Components

### 1. RiskDatasetGenerator

Generates training data from Judge patterns:

```python
from services.reward_model import RiskDatasetGenerator

# Generate full dataset
samples = RiskDatasetGenerator.generate_full_dataset()

# Or specific types
command_samples = RiskDatasetGenerator.generate_command_samples()
intent_samples = RiskDatasetGenerator.generate_intent_samples()
tool_samples = RiskDatasetGenerator.generate_tool_call_samples()

# Save/load
RiskDatasetGenerator.save_dataset(samples, "dataset.jsonl")
loaded = RiskDatasetGenerator.load_dataset("dataset.jsonl")
```

**Training Data Patterns:**

| Category | Pattern | Risk Level |
| -------- | ------- | ---------- |
| Blocked | rm -rf, mkfs, dd, shutdown | HIGH |
| Medium | find -delete, tar -C /, curl --data | MEDIUM |
| Safe | ls, cat, pwd, curl https, python | LOW |

### 2. RewardModel

Trains TF-IDF + LogisticRegression classifier:

```python
from services.reward_model import RewardModel, RiskDatasetGenerator

# Generate dataset
samples = RiskDatasetGenerator.generate_full_dataset()

# Create and train model
model = RewardModel(model_name="liara_v1")
metrics = model.train(samples, test_split=0.2)

print(f"Accuracy: {metrics['test_accuracy']:.2%}")
print(f"Precision: {metrics['test_precision']:.2%}")

# Predict on new input
result = model.predict("ls -la /home")
# Returns: {
#   "eval_binary": 1,           # 1=safe, 0=unsafe
#   "confidence": 0.95,         # confidence in prediction
#   "risk_score": 0.05,         # 0.0=safe, 1.0=unsafe
#   "probability_safe": 0.95,   # P(safe)
#   "probability_unsafe": 0.05  # P(unsafe)
# }

# Batch predictions
results = model.predict_batch(["ls", "rm -rf /", "pwd"])

# Get feature importance
features = model.get_top_features(n=20)
# unsafe_indicators: ["rm", "rf", "/", "mkfs", ...]
# safe_indicators: ["ls", "pwd", "cat", "echo", ...]

# Explain prediction
explanation = model.explain_prediction("rm -rf /home")
# Returns: {
#   "prediction": {"eval_binary": 0, "confidence": 0.92, ...},
#   "top_contributing_features": [
#     {"feature": "rm", "impact": -0.8},
#     {"feature": "rf", "impact": -0.7},
#   ]
# }

# Save/load
model.save("reward_model.pkl")
loaded_model = RewardModel.load("reward_model.pkl")
```

### 3. RewardModelScorer

Provides action/response scoring with Judge integration:

```python
from services.reward_model import RewardModelScorer

scorer = RewardModelScorer(model=trained_model)

# Score an action (pre-action)
action_score = scorer.score_action("sys", "rm -rf /home", context={})
# Returns: {
#   "eval_binary": 0,
#   "risk_score": 0.85,
#   "confidence": 0.92,
#   "model_available": True
# }

# Score a response (post-action)
response_score = scorer.score_response("File deleted", context={})

# Create JudgeCheckResult
check = scorer.create_check_result(action_score, check_name="reward_model_check")

# Boost/reduce confidence
adjusted_conf = scorer.boost_confidence(
    base_confidence=0.7,
    risk_score=0.3,  # Low risk → boost
    boost_factor=0.1
)
# Returns: 0.74 (boosted from 0.7)

# Get explanation
explanation = scorer.get_explanation("rm -rf /")
```

### 4. Judge Adapters

Integrate reward model into judge flow:

```python
from services.judge.adapters.reward_model_pre_action_adapter import RewardModelPreActionAdapter
from services.judge.adapters.reward_model_post_action_adapter import RewardModelPostActionAdapter

# Pre-action: score input before execution
pre_adapter = RewardModelPreActionAdapter(scorer=scorer)
decision = pre_adapter.evaluate_with_reward_score(
    action="sys",
    input_data={"command": "ls -la"},
    context={}
)
# Returns: JudgeDecision(decision="allow", confidence=0.95, ...)

# Post-action: validate response
post_adapter = RewardModelPostActionAdapter(scorer=scorer)
decision = post_adapter.evaluate_with_reward_score(
    action="sys",
    input_data={"command": "ls"},
    result={"output": "file1\nfile2"},
    context={}
)
# Returns: JudgeDecision(decision="allow", confidence=0.92, ...)
```

## Training Workflow

```python
from services.reward_model import RiskDatasetGenerator, RewardModel, RewardModelTrainer

# Step 1: Generate dataset from Judge patterns
samples = RiskDatasetGenerator.generate_full_dataset()

# Step 2: Train model
model, metrics = RewardModelTrainer.train_from_samples(samples)

# Step 3: Evaluate
eval_metrics = RewardModelTrainer.evaluate_predictions(model, samples[:50])
print(f"Accuracy: {eval_metrics['accuracy']:.2%}")
print(f"F1 Score: {eval_metrics['f1']:.2%}")

# Step 4: Save for deployment
model.save("reward_model_v1.pkl")
```

CLI workflow:

```powershell
c:/ai/LIARA/.venv/Scripts/python.exe scripts/train_reward_model.py --print-summary
```

This writes a reusable training bundle to `artifacts/reward_model/`:

- `reward_model.pkl`
- `dataset.jsonl`
- `metrics.json`
- `training_summary.json`

You can also train from a prebuilt JSONL dataset:

```powershell
c:/ai/LIARA/.venv/Scripts/python.exe scripts/train_reward_model.py --dataset-input path/to/dataset.jsonl --output-dir artifacts/reward_model/custom
```

## Deployment

### Configuration

```python
from services.reward_model import RewardModel, RewardModelScorer
from services.judge.adapters.reward_model_pre_action_adapter import RewardModelPreActionAdapter

# Load pre-trained model
model = RewardModel.load("reward_model_v1.pkl")

# Create scorer
scorer = RewardModelScorer(model=model)

# Create judge adapter
adapter = RewardModelPreActionAdapter(scorer=scorer)

# Use in orchestrator
# orchestrator.judge_adapter = adapter
```

### Runtime Integration

Current integration points:

- optional reward-based routing gate in `services/orchestrator/orchestrator.py`
- optional reward-based decision merge in `services/judge/engine.py`
- lazy imports in `services/reward_model/__init__.py` and `services/judge/__init__.py` so the API can start without `scikit-learn`

Configuration knobs:

- `REWARD_MODEL_PATH`
- `REWARD_ROUTING_ENABLED`
- `REWARD_ROUTING_BLOCK_THRESHOLD`
- `REWARD_ROUTING_CONFIDENCE_THRESHOLD`
- `REWARD_JUDGE_ENABLED`
- `REWARD_JUDGE_MODEL_PATH`

### Safety Guarantees

1. **No Execution Risk**: Training uses text patterns, not actual tool outputs
2. **Pre-Built Patterns**: All patterns extracted from Judge's existing blocked list
3. **Graceful Degradation**: If model unavailable, defaults to allow with neutral confidence
4. **Confidence Tracking**: All predictions include confidence scores for audit/logging

## Observed Metrics

Observed local training smoke runs on generated datasets:

| Metric | Value |
| ------ | ----- |
| Earlier generated dataset | 66 samples, test_accuracy ~0.43 |
| Current generated dataset | 188 deduplicated samples, test_accuracy ~0.63 |
| Current generated dataset | train_accuracy ~0.99 |

These values are from the synthetic built-in dataset generator and should be treated as smoke metrics, not as production-grade model validation.

## Integration Points

### Pre-Action Flow

```text
Input → RewardModelPreActionAdapter
  ├─ Extract action text
  ├─ Score with model
  └─ Return JudgeDecision (allow/block)
    └─ Continue/abort execution
```

### Post-Action Flow

```text
Execution Result → RewardModelPostActionAdapter
  ├─ Extract response text
  ├─ Score safety
  └─ Return JudgeDecision
    └─ Accept/revise response
```

## Testing

Current focused verification used during the latest cleanup round:

- reward-model unit suite: 27 tests passing
- judge engine + reward integration suite: 8 tests passing
- routing reward gate tests: focused unit coverage passing

Run tests:

```bash
# Unit tests
pytest tests/unit/test_reward_model.py -v

# Integration tests
pytest tests/integration/test_reward_model_judge_integration.py -v
```

## Notes

- `scikit-learn` is optional for runtime startup and required only for reward-model training/inference paths.
- The trainer CLI is `scripts/train_reward_model.py`.
- Generated artifacts are written to `artifacts/reward_model/` by default.

## Future Enhancements

1. **Multi-Class Classification**: Distinguish between block/warn/allow instead of binary
2. **Online Learning**: Adapt model based on judge decisions over time
3. **Feature Importance Logging**: Track which features drive decisions
4. **Model Versioning**: Support A/B testing of models
5. **Custom Pattern Training**: Allow domain-specific pattern injection
6. **Performance Monitoring**: Track model accuracy on live traffic

## References

- Safe Simulation Mode: [docs/SAFE_SIMULATION_MODE.md](SAFE_SIMULATION_MODE.md)
- Judge Architecture: [docs/ARCHITECTURE.md](ARCHITECTURE.md)
- Service Contracts: [docs/SERVICE_CONTRACTS.md](SERVICE_CONTRACTS.md)
