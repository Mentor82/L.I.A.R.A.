#!/usr/bin/env python3
"""Test if __init__.py is loading .env correctly."""
import sys
import os

print("Before importing Settings:")
print(f"  MEMORY_MODE from os.getenv: {os.getenv('MEMORY_MODE')}\n")

# Now import Settings
from services.config import Settings

print("After importing Settings:")
print(f"  Settings.MEMORY_MODE: {Settings.MEMORY_MODE}")
print(f"  os.getenv('MEMORY_MODE'): {os.getenv('MEMORY_MODE')}")
