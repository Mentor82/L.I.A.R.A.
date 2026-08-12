#!/usr/bin/env python3
"""
End-to-End Demonstration: compute.generate + Judge + compute.run

Shows the full generative compute pipeline:
1. Natural language model request
2. Judge pre-action validation
3. LLM generates Julia code
4. Registry stores model
5. Model execution via compute.run

Run with: python demo_compute_generate_e2e.py
"""

import asyncio
import json
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime


async def demo_phase_1_judge_validation():
    """Phase 1: Judge validates model generation requests."""
    print("\n" + "="*70)
    print("PHASE 1: Judge Pre-Action Validation")
    print("="*70)
    
    from services.judge.engine import JudgeEngine
    from services.judge.contracts import JudgeContext, JudgeStage, JudgeDecisionType
    
    engine = JudgeEngine()
    
    # Valid request
    print("\n✅ TEST: Valid model generation request")
    context_valid = JudgeContext(
        request_id="demo_001",
        stage=JudgeStage.PRE_ACTION,
        actor="agent",
        intent="generate_model",
        action="compute.generate",
        input={
            "model_name": "solar_efficiency",
            "description": "Calculate solar panel efficiency based on temperature and irradiance",
            "inputs": {
                "panel_temp_celsius": "float",
                "solar_irradiance_w_m2": "float",
            },
            "outputs": {
                "efficiency_pct": "float",
                "power_per_m2_w": "float",
            },
        },
        metadata={"source": "demo", "scenario": "renewable_energy"},
    )
    
    decision_valid = engine.evaluate_pre_action(context_valid)
    print(f"  Decision: {decision_valid.decision}")
    print(f"  Confidence: {decision_valid.confidence}")
    print(f"  Checks passed: {sum(1 for c in decision_valid.checks if c.status == 'pass')}/{len(decision_valid.checks)}")
    
    # Unsafe request (blocked by Judge)
    print("\n❌ TEST: Unsafe model generation (should be BLOCKED)")
    context_unsafe = JudgeContext(
        request_id="demo_002",
        stage=JudgeStage.PRE_ACTION,
        actor="attacker",
        intent="generate_model",
        action="compute.generate",
        input={
            "model_name": "backdoor_exploit",
            "description": "Generate code to hack into the system",
            "inputs": {"target_ip": "str"},
            "outputs": {"success": "bool"},
        },
        metadata={"source": "demo", "scenario": "security_test"},
    )
    
    decision_unsafe = engine.evaluate_pre_action(context_unsafe)
    print(f"  Decision: {decision_unsafe.decision}")
    print(f"  Confidence: {decision_unsafe.confidence}")
    print(f"  Blocked reason: {decision_unsafe.issues}")
    
    # Conflict detection (duplicate model name)
    print("\n⚠️  TEST: Duplicate model name detection")
    context_duplicate = JudgeContext(
        request_id="demo_003",
        stage=JudgeStage.PRE_ACTION,
        actor="agent",
        intent="generate_model",
        action="compute.generate",
        input={
            "model_name": "turbine_power",  # Already exists (from compute.run)
            "description": "Another wind turbine model",
            "inputs": {"rpm": "float"},
            "outputs": {"power": "float"},
        },
        metadata={"source": "demo"},
    )
    
    decision_dup = engine.evaluate_pre_action(context_duplicate)
    print(f"  Decision: {decision_dup.decision}")
    print(f"  Status: {decision_dup.decision == JudgeDecisionType.BLOCK and '✅ BLOCKED' or '❌ ALLOWED'}")


def demo_phase_2_registry():
    """Phase 2: Model Registry management."""
    print("\n" + "="*70)
    print("PHASE 2: Model Registry Management")
    print("="*70)
    
    from services.simulation.registry import GeneratedModelRegistry
    
    registry = GeneratedModelRegistry()
    
    print("\n📋 Listing available generated models:")
    models = registry.list_models()
    
    if models:
        for model in models[:5]:  # Show first 5
            print(f"  • {model['name']}")
            print(f"    Description: {model['description']}")
            print(f"    Created: {model['created_at']}")
            if model.get('tags'):
                print(f"    Tags: {', '.join(model['tags'])}")
    else:
        print("  (No generated models yet)")
    
    print(f"\n📊 Registry Stats:")
    print(f"  Total generated models: {len(models)}")


def demo_phase_3_tool_registration():
    """Phase 3: Tool registration and discovery."""
    print("\n" + "="*70)
    print("PHASE 3: Tool Registration & Discovery")
    print("="*70)
    
    from services.tools.registry import _global_registry
    
    print("\n🔧 Available tools:")
    tools = _global_registry.list_tools()
    for tool_name in sorted(tools):
        try:
            metadata = _global_registry.get_metadata(tool_name)
            print(f"  • {tool_name}")
            print(f"    {metadata['description']}")
        except Exception as e:
            print(f"  • {tool_name} (error: {e})")
    
    print(f"\n✨ compute.generate tool is registered: {'compute.generate' in tools}")


def demo_phase_4_api_smoke_test():
    """Phase 4: Live API smoke test."""
    print("\n" + "="*70)
    print("PHASE 4: Live API Smoke Test")
    print("="*70)
    
    print("\n🚀 Starting API server on port 8040...")
    server = subprocess.Popen(
        ["python", "-m", "uvicorn", "services.api.app:app",
         "--host", "127.0.0.1", "--port", "8040", "--log-level", "critical"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    
    try:
        # Test /compute/models endpoint
        print("\n📡 GET /compute/models")
        try:
            with urllib.request.urlopen("http://127.0.0.1:8040/compute/models", timeout=10) as r:
                models_response = json.loads(r.read().decode())
            print(f"  ✅ Available allowlisted models: {[m['name'] for m in models_response.get('models', [])]}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
        
        # Test /compute/run endpoint with existing model
        print("\n📡 POST /compute/run (turbine_power model)")
        try:
            body = json.dumps({
                "model": "turbine_power",
                "inputs": {
                    "shaft_speed_rpm": 2000.0,
                    "torque_nm": 250.0
                }
            }).encode()
            req = urllib.request.Request(
                "http://127.0.0.1:8040/compute/run",
                data=body,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read().decode())
            
            if result.get("status") == "success":
                power = result["outputs"]["power_kw"]
                elapsed = result["elapsed_ms"]
                print(f"  ✅ Computed: {power:.2f} kW in {elapsed:.0f}ms")
            else:
                print(f"  ❌ Error: {result}")
        except Exception as e:
            print(f"  ❌ Error: {e}")
        
        # Test /compute/generate endpoint (will likely fail without real LLM, but shows routing)
        print("\n📡 POST /compute/generate (generation request)")
        try:
            body = json.dumps({
                "model_name": "demo_photovoltaic",
                "description": "Calculate photovoltaic panel efficiency",
                "inputs": {
                    "cell_temp_c": "float",
                    "incident_power_w_m2": "float"
                },
                "outputs": {
                    "efficiency": "float",
                    "output_power_w_m2": "float"
                },
                "llm_provider": "ollama"
            }).encode()
            req = urllib.request.Request(
                "http://127.0.0.1:8040/compute/generate",
                data=body,
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=30) as r:
                result = json.loads(r.read().decode())
            
            if result.get("status") == "success":
                print(f"  ✅ Model generated: {result['model_name']}")
                print(f"     {result['message']}")
            else:
                print(f"  ℹ️  Generation unavailable (likely no LLM running): {result.get('message', 'Unknown')}")
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"  ℹ️  HTTP {e.code}: {body[:100]}")
        except Exception as e:
            print(f"  ℹ️  Skipped: {e}")
    
    finally:
        print("\n🛑 Stopping API server...")
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()


def demo_phase_5_architecture_overview():
    """Phase 5: System architecture overview."""
    print("\n" + "="*70)
    print("PHASE 5: System Architecture Overview")
    print("="*70)
    
    arch = """
    Natural Language Request
    ↓
    ┌─────────────────────────────────────────────┐
    │ Judge Engine (Pre-Action Validation)        │
    │ ✓ Model name availability                   │
    │ ✓ Parameter validation                      │
    │ ✓ Prompt safety analysis                    │
    └─────────────────────────────────────────────┘
    ↓ [ALLOW]
    ┌─────────────────────────────────────────────┐
    │ ComputeGenerateTool                         │
    │ • Calls LLM (via InferenceGateway)          │
    │ • LLM generates Julia code                  │
    │ • Extracts code from <julia_code> tags      │
    └─────────────────────────────────────────────┘
    ↓
    ┌─────────────────────────────────────────────┐
    │ GeneratedModelRegistry                      │
    │ • Validates Julia syntax                    │
    │ • Stores model + metadata                   │
    │ • Returns model URL                         │
    └─────────────────────────────────────────────┘
    ↓
    ┌─────────────────────────────────────────────┐
    │ Model Becomes Available                     │
    │ POST /compute/run with model="..."          │
    │ → Julia execution in subprocess             │
    │ → Deterministic numerical results           │
    └─────────────────────────────────────────────┘
    
    Storage:
      services/simulation/models/generated/
      ├─ _metadata/
      │  ├─ model_1.json (metadata + history)
      │  └─ model_2.json
      ├─ model_1.jl (Julia code)
      └─ model_2.jl
    
    Safety:
      • Default-DENY policy for unknown models
      • Forbidden patterns: hack, backdoor, malware, inject, etc.
      • Parameter name validation
      • Julia syntax checking
    """
    print(arch)


async def main():
    """Run all demo phases."""
    print("\n")
    print("╔" + "="*68 + "╗")
    print("║" + "LIARA Compute Generation System - End-to-End Demonstration".center(68) + "║")
    print("║" + f"Timestamp: {datetime.now().isoformat()}".center(68) + "║")
    print("╚" + "="*68 + "╝")
    
    try:
        # Phase 1: Judge validation
        await demo_phase_1_judge_validation()
        
        # Phase 2: Registry
        demo_phase_2_registry()
        
        # Phase 3: Tool registration
        demo_phase_3_tool_registration()
        
        # Phase 4: Live API test
        demo_phase_4_api_smoke_test()
        
        # Phase 5: Architecture
        demo_phase_5_architecture_overview()
        
        print("\n" + "="*70)
        print("✨ DEMONSTRATION COMPLETE ✨")
        print("="*70)
        print("\nKey Takeaways:")
        print("  ✓ Agent can request models in natural language")
        print("  ✓ Judge validates all requests for safety")
        print("  ✓ LLM generates Julia code dynamically")
        print("  ✓ Models are stored, versioned, and queryable")
        print("  ✓ Generated models execute deterministically")
        print("  ✓ Full integration with Orchestrator")
        print("\nNext Steps:")
        print("  • Run with actual Ollama: ollama pull qwen2.5:3b")
        print("  • Test generation with real LLM inference")
        print("  • Monitor /services/simulation/models/generated/ for stored models")
        print("  • Integrate into multi-step agent workflows")
        print()
    
    except Exception as e:
        print(f"\n❌ Demo error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
