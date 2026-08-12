#!/usr/bin/env python3
"""Test that Postgres memory adapter initializes correctly."""

import asyncio
import os
import sys
from pathlib import Path

# Load environment variables BEFORE importing LIARA modules
from dotenv import load_dotenv
env_path = Path(__file__).parent / ".env"
load_dotenv(str(env_path))

# Add LIARA to path
sys.path.insert(0, str(Path(__file__).parent))

from services.config.settings import Settings
from services.api.app import create_default_memory_adapter


async def test_postgres_adapter():
    """Test Postgres memory adapter initialization."""
    print(f"[Config] MEMORY_MODE: {Settings.MEMORY_MODE}")
    print(f"[Config] POSTGRES_URL: {'*' * 10 if Settings.POSTGRES_URL else 'NOT SET'}")
    print(f"[Config] REDIS_URL: {'*' * 10 if Settings.REDIS_URL else 'NOT SET'}")
    print(f"[Config] QDRANT_URL: {Settings.QDRANT_URL or 'NOT SET'}")
    print()

    print("[TEST] Creating memory adapter...")
    try:
        adapter = create_default_memory_adapter()
        print(f"✓ Adapter created: {adapter.__class__.__name__}")
        print()

        # Check what memory layer we got
        if hasattr(adapter, 'memory_layer'):
            layer = adapter.memory_layer
            print(f"[Memory Layer]")
            print(f"  - session_store: {layer.session_store.__class__.__name__}")
            print(f"  - fact_store: {layer.fact_store.__class__.__name__}")
            print(f"  - retrieval_index: {layer.retrieval_index.__class__.__name__}")
            print()

            # Try to initialize fact_store (Postgres)
            if hasattr(layer.fact_store, '_ensure_initialized'):
                print("[TEST] Initializing FactStore (Postgres)...")
                try:
                    await layer.fact_store._ensure_initialized()
                    print("✓ FactStore initialized successfully")
                except Exception as exc:
                    print(f"✗ FactStore init failed: {exc}")
                    return False
            else:
                print("(fact_store is ephemeral, skipping init)")
            
            # Try a simple write/read test
            print("[TEST] Testing write/read cycle...")
            try:
                test_key = "test:postgres:adapter"
                test_value = {"message": "Hello Postgres!"}
                
                await layer.fact_store.set(test_key, test_value)
                print(f"✓ Write successful: {test_key}")
                
                result = await layer.fact_store.get(test_key)
                print(f"✓ Read successful: {result}")
                
                if result == test_value:
                    print("✓ Data integrity verified (read == written)")
                else:
                    print(f"✗ Data mismatch: {result} != {test_value}")
                    return False
                    
            except Exception as exc:
                print(f"✗ Write/read test failed: {exc}")
                return False

    except Exception as exc:
        print(f"✗ Adapter creation failed: {exc}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 50)
    print("✓ All tests passed!")
    print("=" * 50)
    return True


if __name__ == "__main__":
    success = asyncio.run(test_postgres_adapter())
    sys.exit(0 if success else 1)
