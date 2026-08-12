"""
Configuration settings for AI-Brain standalone service.
Configures DB paths, Qdrant collection, Neo4j connection, and OpenVINO Embedding Worker.
"""

import os
from pathlib import Path

BASE_DIR = Path("c:/ai/LIARA")
STORAGE_DIR = BASE_DIR / "data" / "ai_brain"
STORAGE_DIR.mkdir(parents=True, exist_ok=True)

SQLITE_DB_PATH = STORAGE_DIR / "brain.db"
QDRANT_COLLECTION_NAME = os.getenv("AI_BRAIN_QDRANT_COLLECTION", "ai_brain_vectors")
EMBEDDING_WORKER_URL = os.getenv("AI_BRAIN_EMBEDDING_URL", "http://127.0.0.1:8030/embed")

NEO4J_URI = os.getenv("AI_BRAIN_NEO4J_URI", "bolt://127.0.0.1:7687")
NEO4J_USER = os.getenv("AI_BRAIN_NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("AI_BRAIN_NEO4J_PASSWORD", "liara_password")
NEO4J_DATABASE = os.getenv("AI_BRAIN_NEO4J_DB", "neo4j")
