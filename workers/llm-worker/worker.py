"""Compatibility wrapper for the canonical Redis Streams inference worker.

The implementation lives in ``services.inference.queue``. This module remains
as a stable worker entrypoint for existing imports and scripts.
"""

from __future__ import annotations

from services.inference.queue import RedisStreamsInferenceWorker

__all__ = ["RedisStreamsInferenceWorker"]
