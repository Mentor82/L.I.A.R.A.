# Embedding Worker

Queue-based embedding worker for LIARA.

Note:
- A dedicated HTTP service now exists under `services/embedding/app.py`.
- That service preloads the canonical embedding runtime on startup (with fallback).
- The worker remains available for queue-based processing and uses the same canonical runtime from `services/embedding/engine.py`.

Default model target:
- `OpenVINO/Qwen3-Embedding-0.6B-fp16-ov`

This is the canonical OpenVINO embedding model path.

## Environment

Required:
- `REDIS_URL`

Optional:
- `EMBEDDING_MODEL_ID` (default: `OpenVINO/Qwen3-Embedding-0.6B-fp16-ov`)
- `EMBEDDING_MODEL_DIR` (local model directory; overrides model id)
- `EMBEDDING_DEVICE` (default: `AUTO:NPU`)
- `EMBEDDING_ALLOW_FALLBACK` (allow OpenVINO -> transformers fallback in the shared engine)
- `EMBEDDING_FALLBACK_MODEL_ID` (shared engine fallback model id)
- `EMBEDDING_FALLBACK_DEVICE` (shared engine fallback device)
- `EMBEDDING_QUEUE_REQUEST_STREAM` (default: `liara:embedding:requests`)
- `EMBEDDING_QUEUE_RESPONSE_STREAM_PREFIX` (default: `liara:embedding:responses`)
- `EMBEDDING_QUEUE_CONSUMER_GROUP` (default: `liara-embedding-workers`)
- `EMBEDDING_QUEUE_CONSUMER_NAME` (default: `embedding-worker-1`)
- `EMBEDDING_QUEUE_BLOCK_MS` (default: `1000`)
- `EMBEDDING_WORKER_ALLOW_HASH_FALLBACK` (optional last-resort worker-only fallback)
- `EMBEDDING_WORKER_FALLBACK_DIMENSIONS` (default: `1024`)
- `EMBEDDING_WORKER_MAX_INPUT_CHARS` (default: `8000`)

## Run

```powershell
Set-Location c:/ai/LIARA
& c:/ai/LIARA/.venv/Scripts/python.exe workers/embedding-worker/worker.py
```

## Run Dedicated Service

```powershell
Set-Location c:/ai/LIARA
& c:/ai/LIARA/.venv/Scripts/python.exe -m uvicorn services.embedding.app:app --host 127.0.0.1 --port 8030
```

## Notes

- Uses OpenVINO feature-extraction runtime when available.
- Falls back to the shared transformers runtime when enabled via the canonical engine configuration.
- Falls back to deterministic hash vectors only if `EMBEDDING_WORKER_ALLOW_HASH_FALLBACK=1`.
- Response payload is contract-compatible with `MemoryEmbeddingResponse`.
