# Testuebersicht

Stand: 2026-08-11

## Vision Evidence

Der kanonische MiniCPM-o-Vision-Pfad wurde mit 80 fokussierten Backendtests
und einem erfolgreichen Web-UI-Produktionsbuild geprueft. Abgedeckt sind
Bildnormalisierung, Sandbox- und Remote-URL-Grenze, OpenAI-Data-URI-Mapping,
echter `VLMPipeline.generate(..., images=[Tensor])`-Aufruf, API-History ohne
Base64-Nutzlast sowie der Validator gegen erfundene visuelle Wahrnehmung.

Live-Nachweise vom 2026-08-11:

```text
Direkt :8040: HTTP 200; 128x128; SHA-256 gebunden; load 10.044 ms; gen 14.557 ms
E2E :8011 -> :8010 -> Orchestrator -> :8040 -> Validator: HTTP 200; 187,883 s
Erkannt: blauer Hintergrund und gelber Vollkreis
Run: f72ce48c-2b28-41ae-9a85-9dfd4854d7b8
```

Der anschliessende vollstaendige Unit-Lauf ergab `1363 passed, 5 skipped,
9 failed`. Die neun Abweichungen liegen ausserhalb des Vision-Pfads: zwei
Embedding-Query-Strategietests rufen eine Instanzmethode statisch auf; sieben
NPU-Helper-/Retry-Tests zaehlen den inzwischen vorgeschalteten
Retrieval-Profiler-Aufruf noch nicht mit beziehungsweise erwarten die alte
Provider-Reihenfolge.

## Teststruktur

Aktive Testbereiche:

- `tests/unit/`
- `tests/integration/`
- einzelne Root-Tests wie `test_time_api.py`, `test_settings_loading.py`, `final_integration_test.py`
- Benchmark-/Audit-Skripte unter `scripts/`
- Logs und Benchmark-Ergebnisse unter `logs/tests/`

## Pytest-Konfiguration

`pyproject.toml`:

- `asyncio_mode = "auto"`
- `testpaths = ["tests"]`; Root-Smoke-Skripte werden nur explizit gestartet
- `norecursedirs` schließt Artefakte, Backups, Build-/Dist-Verzeichnisse,
  Scripts, `src/` und WSL-Kandidaten von automatischer Collection aus
- Marker:
  - `live_regression`
  - `live`

## Unit-Test-Themen

Aus dem aktuellen Dateibestand:

- API-App und Chaos-Inputs
- CLI und Textual Chat Cache
- Memory Stores
- Context Controller, Context Scoring, Context Upsert
- Inference Gateway, Invocation, Normalizer, Queue Transport
- Orchestrator Routing, Retry, Reward Routing, Fact Lookup, Embedding Query Strategy
- Judge Contracts, Engine, Pre-/Post-Action-Adapter
- Tools: Builtins, Tool Coordinator, Plot Chart, Policy DB, WSL Executor, Sys Audit
- Reasoning Math und Reasoning Metrics
- Input-Situationsprofil: Analyze/Think/Answer/Plan/Act, Kontext, Mood und Ressourcenbudget
- Attachment Security, Output Sanitizer
- Safe Args Builder und Command Policies
- Native WSL-Session-Lifecycle, Confinement und Cleanup
- MiniCPM-o SpeechPlanner, PCM16-Produzent, WAV-Kompatibilitaet,
  Streamreihenfolge, Backpressure, Cancellation, WebM-/Ogg-Opus-Encoding,
  progressive Ausgabe, Segmentfades, WAV-Tee und 8010-/8040-Proxy

Gemeinsame Test-Doubles für den Graph-v2-Anteil des Memory-Contracts liegen
in `tests/memory_adapter_fakes.py`. Damit müssen fachfremde Tests nicht neun
inaktive Graph-Methoden jeweils separat kopieren; Fakes mit eigenem Graph-
Verhalten müssen weiterhin die exakten keyword-only Signaturen des
`MemoryServiceAdapter` implementieren.

Verifizierte Unit-Baseline vom 2026-07-14:

```text
1189 passed, 4 skipped
```

Die vier Skips liegen ausschließlich in
`tests/unit/test_reasoning_math_julia_parity.py` und werden mit
`RUN_JULIA_PARITY_TESTS=1` explizit aktiviert.

Fokussierter Speech-Streaming-Lauf vom 2026-08-11:

```text
81 passed
```

Abgedeckt sind `test_minicpmo_tts_audio.py`,
`test_minicpmo_tts_engine.py`, `test_tts_service_adapter.py`,
`test_openvino_tts_app.py` und `test_api_app.py`.

Finaler Phase-7B-Lauf nach Low-Latency-Muxing und Tee-Korrektur:

```text
37 passed (Speech-/Codec-/Adapter-Fokus)
9 passed (test_api_app.py -k speech)
```

Live wurden WebM/Opus, Ogg/Opus und PCM16 ueber 8010 -> 8040 sowie der
optionale downloadbare WAV-Tee geprueft. Der WebM-Stream lieferte die ersten
spielbaren Bytes nach 5.463 s bei 14.329 s Gesamtlaufzeit; der bestehende
WAV-Artefaktpfad blieb mit HTTP 200 kompatibel.

## Integrationstests

Aus `tests/integration/`:

- `test_api_flow.py`
- `test_chat_stream_memory_effect_live.py`
- `test_compute_generate_flow.py`
- `test_continue_bridge_attachment_flow.py`
- `test_embedding_worker_live.py`
- `test_inference_live.py`
- `test_inference_queue_mode.py`
- `test_inference_redis_live.py`
- `test_llm_worker_live.py`
- `test_memory_embedding_qdrant_flow_live.py`
- `test_memory_live.py`
- `test_memory_remote_adapter.py`
- `test_memory_service_live.py`
- `test_orchestrator_flow.py`
- `test_orchestrator_judge_flow.py`
- `test_orchestrator_migration_contract.py`
- `test_reward_model_judge_integration.py`
- `test_safety_regression_live.py`
- `test_safe_simulation_mode.py`

## Sinnvolle Smoke-Befehle

Speech-Streaming und kompatibler WAV-Artefaktpfad:

```powershell
.\.venv\Scripts\python.exe scripts\smoke_tts_stream_proxy.py --timeout 360
.\.venv\Scripts\python.exe scripts\smoke_tts_api_proxy.py --timeout 360
```

Same-Origin-Web-BFF vom 2026-08-11:

```text
LAN GET  :3001/api/liara/health                 -> HTTP 200
LAN POST :3001/api/liara/chat/stream            -> SSE progress nach 329 ms
LAN POST :3001/api/liara/speech/stream           -> WebM/Opus, erster Chunk 450 Bytes
X-Liara-Web-Proxy                               -> same-origin/v1
LIARA API :8010                                 -> weiterhin nur 127.0.0.1
Clientabbruch nach erstem SSE-/Audio-Chunk       -> Request geschlossen
```

Der erste reine Next.js-Rewrite wurde verworfen, weil er SSE bis zum finalen
Event puffern konnte. Der produktive Route Handler reicht den Response-Body
als `ReadableStream` weiter.

Wichtig: Bis `testpaths`/`norecursedirs` konfiguriert sind, immer einen
kanonischen Testpfad angeben. Ein nacktes `pytest` rekursiert aktuell in
`artifacts/wsl_sessions/**/candidate` und kann dort ausfuehrbare Testskripte
importieren.

Schnelle Unit-Auswahl:

```powershell
python -m pytest tests/unit/test_api_app.py tests/unit/test_memory_stores.py tests/unit/test_inference_gateway.py
```

Contract-/Adapter-Auswahl:

```powershell
python -m pytest tests/unit/test_orchestration_split.py tests/integration/test_memory_remote_adapter.py
```

WSL-Session-, Executor- und Policy-Regression:

```powershell
python -m pytest -q tests/unit/test_wsl_session_runtime.py tests/unit/test_wsl_executor.py tests/unit/test_sys_command_policy.py
```

Live-Tests nur mit laufenden Services:

```powershell
python -m pytest -m live
```

## Aktueller Befund

Die Testlandschaft ist breit. Viele Tests sind service- oder live-abhaengig.
Fuer lokale Regressionen sollten kleine thematische Testsets bevorzugt werden,
bevor grosse Live- oder Benchmark-Laeufe gestartet werden.

### Reproduzierte Baseline 2026-07-14

```text
python -m pytest --collect-only -q tests
-> 1265 Tests gesammelt in 5,64 s

python -m pytest -q tests/unit
-> 1124 passed, 22 failed, 5 skipped
-> 367,84 s

Workspace/SYS/Audit-Fokus
-> 163 passed in 40,24 s

Pytest-Selektor-Policy, Workspace-Agent und WSL-Executor (2026-07-14)
-> 151 passed in 36,10 s

Workspace-Agent, Memory-Validator, SYS-Policy und WSL-Executor nach
Docker-/WSL-Staging-Haertung (2026-07-14)
-> 170 passed, 2 bestehende datetime-Deprecation-Warnungen in 42,93 s

Nach Einfuehrung des runtime-neutralen `ValidatorExecutionBackend`-Contracts
-> 172 passed, 2 bestehende datetime-Deprecation-Warnungen in 48,39 s

Realer Dispatcher-Smoke mit aktuellem `docker_compose`-Adapter
-> completed, Exit Code 0, 0 Findings in 4503,734 ms
-> 8 Dateien / 6939 Bytes ueber die neutrale Workspace-Vorbereitung

Realer generierter WSL-Workspace
-> `.venv/bin/python -m pytest -q tests`
-> 5 passed in 0,22 s

Realer ai-validator gegen kontrollierten WSL-Snapshot
-> Job `7c895ee9-66e8-4b51-ab9c-0ba182a5f12b`
-> completed, Exit Code 0, 0 Findings in 6528,21 ms
-> 8 Dateien / 6939 Bytes gestaged; Original blieb in Debian-WSL
```

Fehlerbuendel der Unit-Suite:

| Anzahl | Ursache |
| ---: | --- |
| 16 | Testadapter/Fakes implementieren die neun neuen abstrakten Graph-v2-Methoden des Memory-Adapters nicht |
| 1 | API-Fake besitzt das neue `llm_generation`-Attribut nicht |
| 1 | Validator-Test-Mock akzeptiert `session_id` nicht |
| 3 | JuliaBridge-Mocks bilden die aktuelle gestufte WSL-Staging-/Antwortsequenz nicht ab |
| 1 | UTC-Test erwartet das Formatargument vor dem realen `-u`-Argument |

Diese Fehler sind vor weiteren Architekturumbauten zu bereinigen. Dabei muss
pro Gruppe entschieden werden, ob Test oder Produktcontract korrigiert wird;
die Tests duerfen nicht lediglich uebersprungen werden.

Der oben aufgefuehrte Validator-Test-Mock wurde im fokussierten Stand bereits
an den aktuellen `session_id`-Contract angepasst. Die Tabelle bleibt als
historische Baseline des damaligen vollstaendigen Unit-Laufs erhalten; fuer
die verbleibenden Gruppen ist ein neuer Gesamt-Unit-Lauf erforderlich.

### Bekannter Collection-Fehler

```text
python -m pytest --collect-only -q
-> interner Collection-Abbruch durch
   artifacts/wsl_sessions/.../candidate/scripts/test_policy_smoke.py
   und SystemExit
```

Akzeptanz fuer die Reparatur: nacktes `pytest --collect-only` darf nur
kanonische Tests sammeln und muss `artifacts`, `build`, `backups`, `dist` und
WSL-Kandidaten ignorieren.

Am 2026-07-13 wurden fuer die WSL-Session-Integration 123 Unit-, Executor- und
Policy-Tests erfolgreich ausgefuehrt. Zusaetzlich wurde der reale Lifecycle mit
Snapshot, Julia 1.12.6, Session-Mutation, Collection, Audit und Cleanup in WSL
Debian geprueft. Der Validator-Lauf gegen den exportierten Kandidaten hat reale,
bereits bestehende Projekt-/Validatorbefunde sichtbar gemacht; er ist daher
nicht als sauberer Gesamtprojekt-Validatorlauf dokumentiert.
