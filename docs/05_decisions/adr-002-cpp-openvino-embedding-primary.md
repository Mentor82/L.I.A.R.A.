# ADR-002: C++ OpenVINO Embedding Service als Primary

Status: in Umsetzung  
Datum: 2026-05-01  
Kontext: `C:\ai\LIARA-NPU-CLIENT`, `services/embedding/`, LIARA Memory/Retrieval/Orchestrator

## Entscheidung

Embedding wird primaer als dedizierter nativer C++-Dienst betrieben:

```text
LiaraEmbeddingService.exe
-> OpenVINO Runtime
-> NPU bevorzugt
```

Der bestehende Python-Embedding-Service bleibt ausschliesslich:

```text
Fallback / Degraded Path
```

## Begruendung

Embedding ist in LIARA kein Zusatzfeature, sondern zentrale Infrastruktur fuer Retrieval, Routing, Plausibilitaet, Consensus und Verifikation. Der Hot Path soll daher deterministisch, warm geladen und ohne Python-Overhead laufen.

Der vorhandene `LIARA-NPU-CLIENT` liefert bereits passende Bausteine:

- OpenVINO Runtime-DLL-Struktur
- CMake/MSVC/Ninja Build
- Device-/Runtime-Probe
- HTTP/JSON-Servermuster
- Warm-Load- und Readiness-Denke aus Helper/Scheduler

Der Python-Helper diente als Vorlage fuer die C++-Implementierung, nicht als finaler Primaerpfad.

## Betriebsmodus

```text
Primary:
  C++ OpenVINO Embedding Service

Fallback:
  Python Embedding Service
```

Regeln:

```text
if embedding_service not ready
  -> fallback to python

if embedding_service degraded
  -> optional dual-run + compare

if embedding_service stable
  -> python deaktiviert im hot path
```

## Konfigurationsparameter

Folgende Parameter werden explizit konfiguriert, nicht hart im Code verdrahtet:

- Modellpfad
- Device: `NPU`, `GPU`, `CPU`
- Embedding-Dimension: `dims`
- Input-/Output-Shape
- maximale Sequenzlaenge
- Normalisierung Default
- Runtime-/Cache-Verzeichnis
- Fallback-Policy

`dims` kann automatisch erkannt werden, wird aber zur Validierung und Konsistenz zusaetzlich konfiguriert.

Beispiel:

```toml
[server]
host = "127.0.0.1"
port = 8030

[model]
path = "C:/ai/models/OpenVINO/Qwen3-Embedding-0.6B-fp16-ov"
device = "NPU"
max_seq_len = 512
dims = 1024
normalize_default = true

[runtime]
cache_dir = "C:/ai/cache/openvino"
startup_probe = true
allow_python_fallback = true
```

## Runtime-Verhalten

Startup:

```text
Modell laden
-> Shape pruefen
-> dims validieren
-> Device pruefen
-> ready = true
```

Health muss mindestens unterscheiden:

- `loading`
- `ready`
- `failed`
- `degraded`

## Fehlerfaelle

| Fehler | Verhalten |
| --- | --- |
| Model load fail | Service not ready |
| Shape mismatch | Error + Request reject |
| Device not available | Fallback CPU oder Python, je nach Policy |
| Inference error | Error zurueckgeben + loggen |
| Dims mismatch | Error + Request reject |

## Konsistenzanforderung

Alle Komponenten muessen denselben Tokenizer und dasselbe Modell verwenden:

- Scheduler
- Orchestrator
- Consensus
- Memory/Retrieval
- Edge-Analyse-Verifikation

Abweichungen fuehren zu ungueltigen Similarity-Werten und fehlerhaftem Consensus.

## Verwendungsbereiche

Embedding wird zentral genutzt fuer:

- Orchestrator-Vorfilter: RAG / Kandidaten
- Math- und Plausibilitaetsbewertung
- Similarity-Berechnung
- Scheduler Triple-Consensus
- Edge-Analyse-Verifikation

## Performance-Ziel

```text
Embedding < 50 ms im NPU-Zielbereich
Batch Embedding optimiert
Minimaler Overhead
Kein Python im Hot Path
```

## Implementierungszuschnitt

Zielstruktur im NPU-Client:

```text
liara-inference/embedding/
├─ embedding_server.cpp
├─ embedding_engine.cpp
├─ embedding_engine.hpp
├─ embedding_config.hpp
└─ README.md
```

Erster Implementierungsstand liegt unter:

```text
C:\ai\LIARA-NPU-CLIENT\src\openviono\embedding-server
```

Hinweis: Der Pfad folgt aktuell der angeforderten Schreibweise `openviono`.

## Integrationsgrenze

`LiNeP` steht fuer:

```text
Liara Neural Protocol
```

Der native EmbeddingService soll beide Oberflaechen bedienen:

```text
HTTP/API
  -> LIARA API / Orchestrator / Memory Adapter

LiNeP
  -> Scheduler <-> Helper
  -> Scheduler <-> Embedding
```

Wichtig:

```text
Alles, was Orchestrator-owned ist, bleibt ueber API-Contracts.
LiNeP ist die native Scheduler-/Slot-Transportebene und ersetzt nicht die Orchestrator-API.
```

Damit ist der EmbeddingService gleichzeitig API-kompatibler Dienst und nativer Scheduler-Worker.

Aktueller lokaler Implementierungsstand:

- HTTP API ist vorhanden.
- LiNeP UDP Heartbeat fuer Embedding-Slot ist vorhanden.
- LiNeP TCP `EMBED_REQUEST -> EMBED_RESPONSE` ist vorhanden.
- LiNeP Similarity/Consensus ist noch offen.

Wiederzuverwenden aus `LIARA-NPU-CLIENT`:

- CMake Build und Runtime-DLL-Kopierlogik
- OpenVINO Probe/Readiness-Gate
- HTTP/JSON-Muster aus dem Helper
- Device- und Shape-Logik
- `nlohmann/json.hpp`

Nicht fuer Embedding uebernehmen:

- Chat-Prompting
- Decode-Loop
- Instruct/Coder-Routing
- KV-Cache-Annahmen

## Leitsatz

```text
Embedding ist kein Feature.
Embedding ist Infrastruktur.
```
