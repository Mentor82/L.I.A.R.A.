# Embedding Dev Service (OpenVINO Model API)

Experimental embedding service using OpenVINO Model API instead of Optimum Intel.

This service runs on **port 8033** and is designed to test alternative device handling (NPU, GPU, CPU) with better abstraction.

## Features

- OpenVINO Model API for cleaner device handling
- Automatic fallback to transformers if Model API fails
- Same interface as main embedding service
- Health status reporting

## Environment

Optional:
- `EMBEDDING_DEV_MODEL_PATH` (default: `c:/ai/models/Qwen3-Embedding-0.6B-fp16-ov`)
- `EMBEDDING_DEV_DEVICE` (default: `NPU`)
- `EMBEDDING_DEV_STARTUP_TIMEOUT_SECONDS` (default: `120`)
- `EMBEDDING_DEV_FALLBACK_MODEL_ID` (default: `Qwen/Qwen3-Embedding-0.6B`)
- `EMBEDDING_DEV_FALLBACK_DEVICE` (default: `cpu`)

## Start

```powershell
Set-Location C:\ai\LIARA
C:\ai\LIARA\.venv\Scripts\python.exe -m uvicorn services.embedding_dev.app:app --host 0.0.0.0 --port 8033
```

## Test

```bash
curl -X POST http://127.0.0.1:8033/embedding/generate \
  -H "Content-Type: application/json" \
  -d '{"input_text": "hello world", "normalize": true}'

curl http://127.0.0.1:8033/health
```

## Notes

- Model API provides better device abstraction than Optimum Intel for edge devices.
- Uses `create_adapter` from `openvino.model_api.adapters` for model loading.
- Falls back to transformers CPU if Model API encounters runtime issues.
