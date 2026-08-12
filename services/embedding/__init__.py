"""Embedding service package entrypoint."""

from .app import app, create_embedding_service_app

__all__ = ["app", "create_embedding_service_app"]
