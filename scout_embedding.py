"""Scout Embedding Integration: Intent classification via semantic vectors.

Provides hypothesis-based intent profiling using real embeddings:
  1. Cache intent-profile vectors in Redis (Kurzzeit tier) or in-memory
  2. Embed query via external embedding service (:8030 OpenVINO Qwen3-Embedding)
  3. Compute cosine-similarity for intent scoring
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, Set, Union

import httpx
import numpy as np
from pydantic import BaseModel, Field

from services.config import Settings
from services.contracts import RouterDecision

_LOGGER = logging.getLogger("liara.orchestrator.scout_embedding")


class IntentProfile(BaseModel):
    """Typed, versioned DDNA intent profile for Scout semantic routing."""

    name: str
    description: str
    version: str = "v1"
    anchors: Set[str] = Field(default_factory=set)

    def text_representation(self) -> str:
        """Combine description and sorted anchors into a vectorizable text representation."""
        anchor_text = " ".join(sorted(self.anchors))
        return f"{self.name}: {self.description}. {anchor_text}".strip()


class ScoutEmbeddingClient:
    """Manages intent-profile caching and query embedding for Scout hypothesis generation."""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        embedding_service_url: Optional[str] = None,
        intent_profiles: Optional[Dict[str, Union[IntentProfile, Set[str]]]] = None,
    ):
        """
        Args:
            redis_url: Redis connection URL for profile cache (defaults to Settings.REDIS_URL)
            embedding_service_url: Embedding service HTTP base URL (defaults to Settings.SCOUT_EMBEDDING_SERVICE_URL)
            intent_profiles: Intent profiles (IntentProfile objects or raw keyword sets) to vectorize
        """
        self.redis_url = redis_url or getattr(Settings, "REDIS_URL", os.getenv("REDIS_URL"))
        self.embedding_service_url = embedding_service_url or getattr(
            Settings,
            "SCOUT_EMBEDDING_SERVICE_URL",
            os.getenv("SCOUT_EMBEDDING_SERVICE_URL", "http://127.0.0.1:8030"),
        )
        
        # Normalize profiles into IntentProfile instances
        raw_profiles = intent_profiles or {}
        self.intent_profiles: Dict[str, IntentProfile] = {}
        for key, val in raw_profiles.items():
            if isinstance(val, IntentProfile):
                self.intent_profiles[key] = val
            elif isinstance(val, set):
                self.intent_profiles[key] = IntentProfile(
                    name=key,
                    description=f"Intent profile for {key}",
                    anchors=val,
                )

        self._redis_client: Any = None
        self._embedding_cache: Dict[str, np.ndarray] = {}  # In-memory fallback
        self._ready = False
        self._init_error: Optional[str] = None

    async def initialize(self) -> None:
        """Load and cache intent-profile embeddings. Call during orchestrator startup."""
        try:
            # Connect to Redis if configured
            if self.redis_url:
                try:
                    from redis import asyncio as redis_asyncio

                    self._redis_client = redis_asyncio.from_url(self.redis_url, decode_responses=False)
                    await self._redis_client.ping()
                    _LOGGER.info(f"[SCOUT_EMBEDDING] Connected to Redis: {self.redis_url}")
                except Exception as exc:
                    _LOGGER.warning(f"[SCOUT_EMBEDDING] Redis unavailable, using in-memory cache: {exc}")
                    self._redis_client = None
            else:
                _LOGGER.info("[SCOUT_EMBEDDING] No REDIS_URL configured, using in-memory cache")

            # Compute and cache profile embeddings
            await self._cache_intent_profiles()

            self._ready = True
            _LOGGER.info(
                f"[SCOUT_EMBEDDING] Initialized with {len(self.intent_profiles)} intent profiles"
            )

        except Exception as exc:
            self._init_error = str(exc)
            _LOGGER.error(f"[SCOUT_EMBEDDING] Initialization failed: {exc}")
            self._ready = False

    async def _cache_intent_profiles(self) -> None:
        """Vectorize intent profiles and store in Redis (or in-memory)."""
        for intent_name, profile in self.intent_profiles.items():
            profile_text = profile.text_representation()

            try:
                profile_vec = await self._embed_text(profile_text)
            except Exception as exc:
                _LOGGER.warning(
                    f"[SCOUT_EMBEDDING] Failed to embed profile '{intent_name}': {exc}, skipping"
                )
                continue

            # Store in Redis (preferred) or in-memory
            if self._redis_client:
                try:
                    cache_key = f"scout:profile:{intent_name}:{profile.version}"
                    vec_bytes = profile_vec.astype(np.float32).tobytes()
                    await self._redis_client.set(cache_key, vec_bytes)
                except Exception as exc:
                    _LOGGER.warning(f"[SCOUT_EMBEDDING] Redis cache failed for '{intent_name}': {exc}")
                    self._embedding_cache[intent_name] = profile_vec
            else:
                self._embedding_cache[intent_name] = profile_vec

            _LOGGER.debug(
                f"[SCOUT_EMBEDDING] Cached profile '{intent_name}' ({profile.version}): shape {profile_vec.shape}"
            )

    async def _embed_text(self, text: str) -> np.ndarray:
        """Call external embedding service (:8030) to vectorize text."""
        try:
            async with httpx.AsyncClient(timeout=Settings.EMBEDDING_SERVICE_TIMEOUT_SECONDS) as client:
                payloads = (
                    {"input_text": text, "normalize": True},
                    {"text": text},
                )

                data: Optional[dict] = None
                last_exc: Optional[Exception] = None
                for payload in payloads:
                    try:
                        response = await client.post(
                            f"{self.embedding_service_url}/embedding/generate",
                            json=payload,
                        )
                        response.raise_for_status()
                        data = response.json()
                        break
                    except Exception as exc:
                        last_exc = exc
                        continue

                if data is None:
                    if last_exc is not None:
                        raise last_exc
                    raise RuntimeError("embedding_request_failed")

                vector_data = None
                if isinstance(data, dict):
                    if "vector" in data:
                        vector_data = data.get("vector")
                    elif "embedding" in data:
                        vector_data = data.get("embedding")
                    elif isinstance(data.get("item"), dict):
                        item = data.get("item") or {}
                        vector_data = item.get("embedding") or item.get("vector")

                if not vector_data:
                    raise ValueError(f"Unknown embedding response format: {list(data.keys())}")

                return np.array(vector_data, dtype=np.float32)

        except Exception as exc:
            _LOGGER.error(f"[SCOUT_EMBEDDING] Embedding service call failed: {exc}")
            raise

    async def get_profile_embedding(self, intent_name: str) -> Optional[np.ndarray]:
        """Retrieve cached intent-profile embedding."""
        if not self._ready:
            return None

        profile = self.intent_profiles.get(intent_name)
        version = profile.version if profile else "v1"

        if self._redis_client:
            try:
                cache_key = f"scout:profile:{intent_name}:{version}"
                vec_bytes = await self._redis_client.get(cache_key)
                if vec_bytes:
                    return np.frombuffer(vec_bytes, dtype=np.float32)
            except Exception as exc:
                _LOGGER.warning(f"[SCOUT_EMBEDDING] Redis retrieval failed for '{intent_name}': {exc}")

        return self._embedding_cache.get(intent_name)

    async def embed_query(self, query: str) -> Optional[np.ndarray]:
        """Embed user query."""
        if not self._ready:
            return None

        try:
            return await self._embed_text(query)
        except Exception as exc:
            _LOGGER.error(f"[SCOUT_EMBEDDING] Failed to embed query: {exc}")
            return None

    @staticmethod
    def cosine_similarity(vec_a: np.ndarray, vec_b: np.ndarray) -> float:
        """Compute cosine similarity between two vectors."""
        norm_a = np.linalg.norm(vec_a)
        norm_b = np.linalg.norm(vec_b)

        if norm_a == 0 or norm_b == 0:
            return 0.0

        return float(np.dot(vec_a, vec_b) / (norm_a * norm_b))

    async def score_intents(self, query: str) -> Dict[str, float]:
        """Compute intent scores for a query using embedding-based similarity.

        Returns:
            Dict mapping intent_name -> cosine_similarity_score (0.0-1.0)
        """
        if not self._ready:
            return {}

        query_vec = await self.embed_query(query)
        if query_vec is None:
            return {}

        scores = {}
        for intent_name in self.intent_profiles.keys():
            profile_vec = await self.get_profile_embedding(intent_name)
            if profile_vec is not None:
                score = self.cosine_similarity(query_vec, profile_vec)
                scores[intent_name] = score

        return scores

    async def close(self) -> None:
        """Clean up Redis connection."""
        if self._redis_client:
            try:
                await self._redis_client.aclose()
            except Exception as exc:
                _LOGGER.warning(f"[SCOUT_EMBEDDING] Redis close failed: {exc}")
