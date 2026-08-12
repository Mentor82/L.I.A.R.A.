# LiaraEmbeddingService

---

**Der EmbeddingService ist multi-protokollfähig, aber nicht multi-instanzfähig geplant.
Es existiert genau ein logischer EmbeddingService mit Worker-ID 30. 
HTTP bleibt die feste Schnittstelle zur Liara-Orchestrator-Kette. LiNeP dient als interne Worker-Schnittstelle für den HelperScheduler.**

---

Native C++ OpenVINO embedding service.

Status: first implementation target for the OpenVINO primary path. Python remains
the fallback/degraded path in LIARA until this service is fully validated.

This folder is a local LIARA snapshot of the relevant `LIARA-NPU-CLIENT`
embedding context. It contains the embedding server source plus the surrounding
protocol/runtime context needed to understand the direction:

```text
.
|- embedding_*.cpp/.hpp        # LiaraEmbeddingService source
|- linep/                      # copied liara-linep source/include/tests, no build artifacts
|- liara-inference/            # copied common helper/inference contracts/templates
|- docs/npu-client/            # copied NPU-client architecture/runbook/ADR docs
`- vendor/nlohmann/json.hpp    # single-header JSON dependency
```

## Integration Boundary

`LiNeP` means `Liara Neural Protocol`.

Both surfaces are required:

```text
HTTP/API
  -> compatibility surface for LIARA API / Orchestrator / Memory Adapter

LiNeP
  -> native scheduler transport for Scheduler <-> Helper
  -> native scheduler transport for Scheduler <-> Embedding
```

Important boundary:

```text
Anything Orchestrator-owned continues to go through API contracts.
Native LiNeP is for scheduler-side worker/slot transport, not for bypassing the Orchestrator API.
```

Target direction:

```text
Liara API / Orchestrator
  -> HTTP embedding contract

Scheduler
  -> LiNeP EMBED_REQUEST / EMBED_RESPONSE
  -> LiNeP SIMILARITY_REQUEST / SIMILARITY_RESPONSE
  -> LiNeP CONSENSUS_REQUEST / CONSENSUS_RESPONSE
```

Implemented now:

```text
LiNeP UDP Heartbeat
LiNeP TCP EMBED_REQUEST -> EMBED_RESPONSE
```

Configured via:

```toml
[linep]
enabled = false
heartbeat_host = "127.0.0.1"
heartbeat_port = 19001
tcp_port = 19002
heartbeat_interval_ms = 1000
worker_id = 30
slot_id = 0
```

## Endpoints

```text
GET  /health
POST /embedding/generate
```

`POST /embedding/generate` follows the LIARA memory embedding response shape:

```json
{
  "input_text": "text",
  "normalize": true,
  "metadata": {}
}
```

## Build

From `C:\ai\LIARA`:

```powershell
cmake -S src/emeddingserver -B src/emeddingserver/build -DOpenVINO_DIR=C:\Users\WM\AppData\Roaming\Python\Python312\site-packages\openvino\cmake
cmake --build src/emeddingserver/build --target LiaraEmbeddingService
```

## Run

```powershell
.\workers\embedding\exec\bin\LiaraEmbeddingService.exe --config=workers\embedding\exec\conf\embedding_config.toml
```

## Notes

- Device is explicit; avoid `AUTO`.
- `dims` is config-driven. If `dims = 0`, the service tries to derive it from the compiled output shape.
- Current implementation provides the native service skeleton, config, readiness, shape/dim validation, OpenVINO tokenizer loading from `openvino_tokenizer.xml`, and the OpenVINO infer path.
- `linep.enabled=true` starts the native scheduler-side embedding slot: UDP heartbeat plus TCP `EMBED_REQUEST` handler.
- If the model expects token tensors, `openvino_tokenizer.xml` is required next to the model. The service refuses zero-filled token tensors because that would break semantic similarity consistency with the Python worker.
