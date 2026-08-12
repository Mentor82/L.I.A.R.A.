#!/usr/bin/env python
"""Test embedding service startup and identify issues."""

import sys
import traceback
from pathlib import Path

# Add repo to path
sys.path.insert(0, str(Path(__file__).parent))

print("=" * 80)
print("EMBEDDING SERVICE STARTUP DIAGNOSIS")
print("=" * 80)

# Test imports
print("\n[1] Testing imports...")
deps = {
    "openvino": None,
    "transformers": None,
    "optimum": None,
    "uvicorn": None,
    "services.embedding.engine": None,
    "services.embedding.app": None,
}

for name in deps:
    try:
        mod = __import__(name, fromlist=[name.split(".")[-1]])
        deps[name] = "✓"
        print(f"  {name:40} ✓")
    except Exception as e:
        deps[name] = str(e)
        print(f"  {name:40} ✗ {type(e).__name__}: {str(e)[:60]}")

# Test environment
print("\n[2] Testing environment variables...")
import os
from dotenv import load_dotenv

load_dotenv()

env_vars = [
    "EMBEDDING_MODEL_DIR",
    "EMBEDDING_MODEL_ID",
    "EMBEDDING_DEVICE",
    "EMBEDDING_BACKEND",
    "EMBEDDING_ALLOW_FALLBACK",
    "EMBEDDING_STARTUP_TIMEOUT_SECONDS",
]

for var in env_vars:
    val = os.getenv(var, "<not set>")
    print(f"  {var:40} {val}")

# Test engine creation
print("\n[3] Testing engine creation...")
try:
    from services.embedding.engine import EmbeddingEngineConfig, OpenVINOEmbeddingEngine
    
    model_dir = os.getenv("EMBEDDING_MODEL_DIR") or "c:/ai/models"
    model_id = os.getenv("EMBEDDING_MODEL_ID") or "OpenVINO/Qwen3-Embedding-0.6B-fp16-ov"
    device = os.getenv("EMBEDDING_DEVICE", "AUTO:NPU")
    
    config = EmbeddingEngineConfig(
        model_id=model_id,
        model_dir=model_dir,
        device=device,
        allow_fallback=True,
    )
    print(f"  Config created: {config}")
    
    engine = OpenVINOEmbeddingEngine(config)
    print(f"  Engine created: {engine}")
    print(f"  Available: {engine.is_available()}")
    
    if not engine.is_available():
        print(f"  Status: {engine.status()}")
    
except Exception as e:
    print(f"  ✗ Error: {e}")
    traceback.print_exc()

# Test app creation
print("\n[4] Testing app creation...")
try:
    from services.embedding.app import create_embedding_service_app
    
    app = create_embedding_service_app()
    print(f"  App created: {app}")
    print(f"  Title: {app.title}")
    print(f"  Routes: {len(app.routes)}")
    for route in app.routes:
        print(f"    - {route.path}")
    
except Exception as e:
    print(f"  ✗ Error: {e}")
    traceback.print_exc()

print("\n" + "=" * 80)
print("DIAGNOSIS COMPLETE")
print("=" * 80)
