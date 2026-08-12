"""Embedding worker for Redis Streams embedding processing.

The queue transport lives here. The embedding runtime is delegated to the
canonical implementation in ``services.embedding.engine``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, cast

# Ensure repo root is importable when run as a standalone worker process.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.contracts import (  # noqa: E402
    EmbeddingVector,
    MemoryEmbeddingRequest,
    MemoryEmbeddingResponse,
    MemoryServiceStatus,
)


class OpenVINOEmbeddingEngine:
    """Generates embeddings using OpenVINO-capable HF models.

    If runtime dependencies are missing, falls back to deterministic hash vectors.
    """

    def __init__(
        self,
        *,
        model_id: str,
        model_dir: str | None = None,
        device: str = "NPU",
        allow_hash_fallback: bool = False,
        fallback_dimensions: int = 1024,
        max_length: int = 512,
    ):
        self.model_id = model_id
        self.model_dir = model_dir
        self.device = device
        self.allow_hash_fallback = allow_hash_fallback
        self.fallback_dimensions = max(8, int(fallback_dimensions))
        self.max_length = max(1, int(max_length))
        self._tokenizer: Any | None = None
        self._model: Any | None = None
        self._runtime_error: str | None = None

    def _ensure_runtime(self) -> None:
        if self._tokenizer is not None and self._model is not None:
            return
        if self._runtime_error is not None:
            raise RuntimeError(self._runtime_error)

        try:
            import openvino as ov  # type: ignore
            from transformers import AutoTokenizer  # type: ignore
        except Exception as exc:  # pragma: no cover - env dependent
            self._runtime_error = (
                "Missing runtime for OpenVINO embeddings. "
                "Install: pip install openvino transformers"
            )
            raise RuntimeError(self._runtime_error) from exc

        source = self.model_dir or self.model_id
        try:
            model_xml = source
            if os.path.isdir(model_xml):
                candidate = os.path.join(model_xml, "openvino_model.xml")
                if os.path.isfile(candidate):
                    model_xml = candidate

            core = ov.Core()
            model = core.read_model(model_xml)
            input_names = {inp.any_name for inp in model.inputs}
            reshape_map: dict[str, list[int]] = {}
            if "input_ids" in input_names:
                reshape_map["input_ids"] = [1, self.max_length]
            if "attention_mask" in input_names:
                reshape_map["attention_mask"] = [1, self.max_length]
            if "token_type_ids" in input_names:
                reshape_map["token_type_ids"] = [1, self.max_length]
            if reshape_map:
                model.reshape(reshape_map)

            self._tokenizer = AutoTokenizer.from_pretrained(
                source,
                padding_side="left",
                fix_mistral_regex=True,
            )
            self._model = core.compile_model(model, self.device)
        except Exception as exc:  # pragma: no cover - env dependent
            self._runtime_error = f"Failed to load embedding model '{source}': {exc}"
            raise RuntimeError(self._runtime_error) from exc

    @staticmethod
    def _hash_fallback_vector(text: str, dimensions: int = 1024) -> list[float]:
        """Deterministic fallback vector to keep contract stable when runtime is absent."""
        base = hashlib.sha256(text.encode("utf-8")).digest()
        vals: list[float] = []
        while len(vals) < dimensions:
            for b in base:
                vals.append((b / 255.0) * 2.0 - 1.0)
                if len(vals) == dimensions:
                    break
            base = hashlib.sha256(base).digest()
        return vals

    def embed(self, text: str, normalize: bool = True) -> tuple[list[float], dict[str, Any]]:
        try:
            import numpy as np

            self._ensure_runtime()
            tokenizer = cast(Any, self._tokenizer)
            model = cast(Any, self._model)

            tokens = tokenizer(
                [text],
                return_tensors="np",
                truncation=True,
                max_length=self.max_length,
                padding="max_length",
            )

            inputs = {
                key: value
                for key, value in tokens.items()
                if key in {"input_ids", "attention_mask", "token_type_ids"}
            }

            infer_req = model.create_infer_request()
            infer_req.infer(inputs)
            arr = infer_req.get_output_tensor().data

            if arr.ndim == 3:
                attention_mask = tokens["attention_mask"]
                left_padding = bool(np.all(attention_mask[:, -1] == 1))
                if left_padding:
                    pooled = arr[:, -1, :]
                else:
                    seq_lens = attention_mask.sum(axis=1) - 1
                    pooled = arr[np.arange(arr.shape[0]), seq_lens]
                vector = pooled[0].tolist()
            elif arr.ndim == 2:
                vector = arr[0].tolist()
            else:
                vector = arr.flatten().tolist()

            if normalize:
                norm = sum(x * x for x in vector) ** 0.5
                if norm > 0:
                    vector = [x / norm for x in vector]

            return vector, {"backend": "openvino", "device": self.device, "fallback": False}
        except Exception as exc:
            if not self.allow_hash_fallback:
                raise
            vector = self._hash_fallback_vector(text, dimensions=self.fallback_dimensions)
            if normalize:
                norm = sum(x * x for x in vector) ** 0.5
                if norm > 0:
                    vector = [x / norm for x in vector]
            return vector, {
                "backend": "hash-fallback",
                "device": "cpu",
                "fallback": True,
                "reason": str(exc),
                "fallback_dimensions": self.fallback_dimensions,
            }


class RedisStreamsEmbeddingWorker:
    """Redis Streams request-response worker for embedding generation."""

    def __init__(
        self,
        engine: OpenVINOEmbeddingEngine,
        *,
        redis_url: str,
        request_stream: str,
        response_stream_prefix: str,
        consumer_group: str,
        consumer_name: str = "embedding-worker-1",
        block_ms: int = 1000,
        max_input_chars: int = 8000,
        client: Any = None,
    ):
        self.engine = engine
        self.redis_url = redis_url
        self.request_stream = request_stream
        self.response_stream_prefix = response_stream_prefix
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.block_ms = block_ms
        self.max_input_chars = max(256, int(max_input_chars))
        self._client = client
        self._owns_client = False
        self._group_ready = False
        self._logger = logging.getLogger("liara.embedding.worker")

    async def _ensure_client(self) -> Any:
        if self._client is not None:
            return self._client

        try:
            from redis import asyncio as redis_asyncio  # type: ignore
        except ImportError as exc:
            raise RuntimeError("redis package is required for RedisStreamsEmbeddingWorker") from exc

        self._client = redis_asyncio.from_url(self.redis_url, decode_responses=False)
        self._owns_client = True
        return self._client

    async def close(self) -> None:
        if self._client is None or not self._owns_client:
            return
        try:
            aclose = getattr(self._client, "aclose", None)
            if callable(aclose):
                maybe_awaitable = aclose()
                if hasattr(maybe_awaitable, "__await__"):
                    await cast(Awaitable[Any], maybe_awaitable)
            else:
                await self._client.close()
        finally:
            self._client = None
            self._owns_client = False
            self._group_ready = False

    async def _ensure_group(self) -> None:
        if self._group_ready:
            return
        client = await self._ensure_client()
        try:
            await client.xgroup_create(
                self.request_stream,
                self.consumer_group,
                id="0-0",
                mkstream=True,
            )
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        self._group_ready = True

    @staticmethod
    def _decode_fields(fields: dict[Any, Any]) -> dict[str, str]:
        decoded: dict[str, str] = {}
        for key, value in fields.items():
            if isinstance(key, (bytes, bytearray)):
                key = key.decode("utf-8")
            if isinstance(value, (bytes, bytearray)):
                value = value.decode("utf-8")
            decoded[str(key)] = str(value)
        return decoded

    def _reply_stream(self, correlation_id: str) -> str:
        return f"{self.response_stream_prefix}:{correlation_id}"

    def _build_response(self, request: MemoryEmbeddingRequest) -> MemoryEmbeddingResponse:
        text = request.input_text or ""
        truncated = len(text) > self.max_input_chars
        if truncated:
            text = text[: self.max_input_chars]

        vector, meta = self.engine.embed(text, normalize=request.normalize)
        status = MemoryServiceStatus(
            status="success",
            backend="embedding",
            degraded=bool(meta.get("fallback", False)),
            error=None,
            metadata=meta,
        )
        item = EmbeddingVector(
            model=request.model or self.engine.model_id,
            dimensions=len(vector),
            vector=vector,
            metadata={
                **request.metadata,
                **meta,
                "input_truncated": truncated,
                "max_input_chars": self.max_input_chars,
            },
        )
        return MemoryEmbeddingResponse(item=item, status=status)

    async def process_next(self, timeout_seconds: float = 1.0) -> bool:
        await self._ensure_group()
        client = await self._ensure_client()

        block_ms = max(1, min(self.block_ms, int(timeout_seconds * 1000)))
        messages = await client.xreadgroup(
            self.consumer_group,
            self.consumer_name,
            {self.request_stream: ">"},
            count=1,
            block=block_ms,
        )
        if not messages:
            return False

        for _stream_name, entries in messages:
            for message_id, fields in entries:
                decoded = self._decode_fields(fields)
                correlation_id = decoded.get("correlation_id", "")
                reply_stream = decoded.get("reply_stream") or self._reply_stream(correlation_id)
                raw_request = decoded.get("request", "{}")

                try:
                    request = MemoryEmbeddingRequest.model_validate(json.loads(raw_request))
                    response = self._build_response(request)
                    payload = response.model_dump()
                except Exception as exc:
                    payload = MemoryEmbeddingResponse(
                        item=None,
                        status=MemoryServiceStatus(
                            status="failed",
                            backend="embedding",
                            degraded=True,
                            error=str(exc),
                            metadata={"worker": "embedding"},
                        ),
                    ).model_dump()

                await client.xadd(
                    reply_stream,
                    {
                        "correlation_id": correlation_id,
                        "result": json.dumps(payload),
                    },
                )
                await client.xack(self.request_stream, self.consumer_group, message_id)
                return True

        return False

    async def serve_forever(self, poll_timeout_seconds: float = 1.0) -> None:
        backoff_seconds = 0.5
        while True:
            try:
                await self.process_next(timeout_seconds=poll_timeout_seconds)
                backoff_seconds = 0.5
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._logger.warning("embedding worker loop error: %s", exc)
                self._group_ready = False
                try:
                    await self.close()
                except Exception:
                    pass
                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2.0, 10.0)


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def _main() -> None:
    logging.basicConfig(
        level=os.getenv("EMBEDDING_WORKER_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    model_id = _env("EMBEDDING_MODEL_ID", "OpenVINO/Qwen3-Embedding-0.6B-fp16-ov")
    model_dir = os.getenv("EMBEDDING_MODEL_DIR") or None
    device = _env("EMBEDDING_DEVICE", "NPU")
    allow_hash_fallback = _env_bool("EMBEDDING_WORKER_ALLOW_HASH_FALLBACK", False)
    fallback_dimensions = int(_env("EMBEDDING_WORKER_FALLBACK_DIMENSIONS", _env("QDRANT_VECTOR_SIZE", "1024")))
    max_input_chars = int(_env("EMBEDDING_WORKER_MAX_INPUT_CHARS", "8000"))
    max_length = int(_env("EMBEDDING_MAX_LENGTH", "512"))

    redis_url = _env("REDIS_URL", "")
    if not redis_url:
        raise RuntimeError("REDIS_URL is required for embedding worker")

    worker = RedisStreamsEmbeddingWorker(
        OpenVINOEmbeddingEngine(
            model_id=model_id,
            model_dir=model_dir,
            device=device,
            allow_hash_fallback=allow_hash_fallback,
            fallback_dimensions=fallback_dimensions,
            max_length=max_length,
        ),
        redis_url=redis_url,
        request_stream=_env("EMBEDDING_QUEUE_REQUEST_STREAM", "liara:embedding:requests"),
        response_stream_prefix=_env("EMBEDDING_QUEUE_RESPONSE_STREAM_PREFIX", "liara:embedding:responses"),
        consumer_group=_env("EMBEDDING_QUEUE_CONSUMER_GROUP", "liara-embedding-workers"),
        block_ms=int(_env("EMBEDDING_QUEUE_BLOCK_MS", "1000")),
        consumer_name=_env("EMBEDDING_QUEUE_CONSUMER_NAME", "embedding-worker-1"),
        max_input_chars=max_input_chars,
    )
    await worker.serve_forever()


if __name__ == "__main__":
    asyncio.run(_main())
