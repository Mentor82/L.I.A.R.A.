# Service: liara-inference

Stand: 2026-07-14  
Code: `services/inference/`

## Aufgabe

`liara-inference` normalisiert und routet LLM-/Generierungsanfragen an verschiedene Provider. Der aktuelle primaere Runtime-Pfad ist `llama.cpp`, da der lokale native llama.cpp/SYCL-Pfad genutzt wird.

## Provider

Aktuell im `InferenceGateway` registriert:

- `llama_cpp`
- `ollama`
- `ollama_gpu`
- `ollama_cpu`
- `openvino`
- `openvino_npu_helper`

Primaer:

- `llama_cpp`

Fallbacks/Alternativen:

- `ollama`, `ollama_gpu`, `ollama_cpu`
- `openvino`
- `openvino_npu_helper` fuer Helper-Aufgaben

Zusaetzliche Module:

- `llama_cpp_server.py` fuer lokale llama.cpp Serververwaltung
- `invocation.py` fuer direkte oder Queue-basierte Invocation
- `queue.py` fuer Redis-Stream-basierte Inferenzqueue
- `normalizer.py` fuer Response-/Stream-Normalisierung
- `openvino_npu_app.py` als eigener NPU-Helper-HTTP-Service

## Gateway-Verhalten

Das Gateway verwendet einen Circuit Breaker pro Provider:

- Fehlerzaehler
- Open/Cooldown-Zeit
- Half-open Probe
- Breaker-Metadaten im Ergebnis

Provider-Auswahl laeuft ueber `request.provider` oder `DEFAULT_LLM_PROVIDER`. Bei Fallback-/Hybrid-Pfaden wird `llama_cpp` zuerst versucht; Ollama-Varianten dienen danach als Fallback. Co-Worker-Anfragen sind standardmaessig auf `CO_WORKER_MAIN_PROVIDER=llama_cpp` gelockt.

## Konfiguration

Wichtige ENV-Werte:

- `DEFAULT_LLM_PROVIDER`
- `LLAMA_CPP_BASE_URL`
- `LLAMA_CPP_MODEL`
- `LLAMA_CPP_TIMEOUT_SECONDS`
- `LLAMA_CPP_BUILD_BASE_DIR`
- `LLAMA_CPP_BUILD_VARIANT`
- `LLAMA_CPP_MANAGED_BY_API` (`true` im regulaeren API-Start; fuer isolierte
  Test-API-Instanzen auf `false` setzen)
- `OLLAMA_HOST`
- `OLLAMA_PORT`
- `OLLAMA_MODEL`
- `OLLAMA_GPU_*`
- `OLLAMA_CPU_*`
- `OPENVINO_GENAI_MODEL_DIR`
- `OPENVINO_GENAI_DEVICE`
- `OPENVINO_NPU_BASE_URL`
- `INFERENCE_BREAKER_ENABLED`
- `INFERENCE_QUEUE_*`

## NPU Helper

`services/inference/openvino_npu_app.py` bietet:

- `GET /health`
- `POST /infer`
- `POST /infer/helper`

Der Orchestrator kann kleine Aufgaben an diesen Helper auslagern, wenn `NPU_HELPER_OFFLOAD_ENABLED` aktiv ist.

## Aktueller Befund

Inference ist als Provider-Abstraktion implementiert. Im Snapshot vom
2026-07-14 waren llama.cpp und Ollama aktiv; die lokale `.env` waehlte Ollama,
der Code-Default ist `ll_ol_fallback`, Compose setzt `hybrid`. Diese
Konfigurationsdrift muss vor einer allgemeinen Aussage ueber den Primaerpfad
beseitigt werden. Der NPU-Helper-Adapter ist implementiert, sein Dienst auf
Port 8040 war jedoch nicht aktiv. Helper-Ausfall besitzt einen Main-Provider-
Fallback, kann aber unnoetige Latenz erzeugen.

Temporäre API-Instanzen duerfen nicht in den produktiven Inferenz-Lifecycle
eingreifen. `scripts/compute_run_api_smoke_test.py --with-server` deaktiviert
deshalb die API-seitige llama.cpp-Verwaltung. Der regulaere API-Start behaelt
den bisherigen Default und verwaltet llama.cpp weiterhin, solange
`LLAMA_CPP_MANAGED_BY_API` nicht explizit deaktiviert wird.
