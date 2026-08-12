"""Unit checks for transitional top-level service entrypoints."""

from services.embedding.app import app as embedding_app
from services.memory.app import app as memory_app


def test_memory_entrypoint_imports_app():
    assert memory_app.title == "liara-memory"


def test_embedding_entrypoint_imports_app():
    assert embedding_app.title == "liara-embedding"
