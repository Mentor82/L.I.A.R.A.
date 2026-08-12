#!/usr/bin/env python3
import os
from pathlib import Path
from dotenv import load_dotenv

print("Before load_dotenv:")
print(f"  MEMORY_MODE={os.getenv('MEMORY_MODE')}")
print(f"  POSTGRES_URL={os.getenv('POSTGRES_URL')}")
print()

# Get the correct path
env_path = Path(__file__).parent / ".env"
print(f".env path: {env_path}")
print(f".env exists: {env_path.exists()}")
print()

load_dotenv(str(env_path))

print("After load_dotenv:")
print(f"  MEMORY_MODE={os.getenv('MEMORY_MODE')}")
print(f"  POSTGRES_URL={os.getenv('POSTGRES_URL')}")

