#!/usr/bin/env python3
"""Check if all backend URLs are configured in Settings."""
from services.config import Settings

print("Backend URLs Configuration:")
print(f"  MEMORY_MODE: {Settings.MEMORY_MODE}")
print(f"  POSTGRES_URL: {Settings.POSTGRES_URL}")
print(f"  REDIS_URL: {Settings.REDIS_URL}")
print(f"  QDRANT_URL: {Settings.QDRANT_URL}")
print()

if Settings.MEMORY_MODE == "postgres":
    if Settings.POSTGRES_URL:
        print("✅ Postgres: Configured")
    else:
        print("❌ Postgres: NOT configured")
        
    if Settings.REDIS_URL:
        print("✅ Redis: Configured") 
    else:
        print("❌ Redis: NOT configured")
        
    if Settings.QDRANT_URL:
        print("✅ Qdrant: Configured")
    else:
        print("❌ Qdrant: NOT configured")
else:
    print(f"⚠️ Not in postgres mode: {Settings.MEMORY_MODE}")
