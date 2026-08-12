## Workers Scaffold

Future worker processes for model, embedding, and vision execution.


Current worker scaffolds:
- `workers/llm-worker/worker.py` as a compatibility entrypoint for the canonical Redis Streams inference worker in `services/inference/queue.py`.
- `workers/embedding-worker/worker.py` for Redis Streams embedding processing using the canonical embedding runtime from `services/embedding/engine.py`.

Native embedding service deployment slot:
- `workers/embedding/exec/` — runtime directory for `LiaraEmbeddingService` (C++ OpenVINO primary path)
  - `bin/` — `LiaraEmbeddingService.exe`
  - `lib/` — OpenVINO Runtime DLLs and native dependencies (manually populated)
  - `conf/` — `embedding_config.toml`
  - `log/` — runtime logs

Embedding worker default model:
- `OpenVINO/Qwen3-Embedding-0.6B-fp16-ov`
