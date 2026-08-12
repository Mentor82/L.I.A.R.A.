# ADR-003: MiniCPM-o Audio/TTS als OpenVINO-Servicefaehigkeit

Status: angenommen, Phase 0 bis 3, Phase 6, Phase 7A und Phase 7B umgesetzt; Phase-4-Spike abgeschlossen, Gate nicht bestanden  
Datum: 2026-08-09  
Kontext: `services/inference/openvino_npu_app.py`, Port 8040, MiniCPM-o INT4 Audio/TTS-Audit `20260809_212855`

## Umsetzungsstand

Stand 2026-08-11:

- Phase 0 abgeschlossen: selbsttragender ChatTTS-Tokenizer, `neutral-v1`
  Sprecherprofil und SHA-256-Runtime-Manifest liegen im auditierten Bundle.
- Phase 1 abgeschlossen: tensorreine, injizierbare CPU-Runtime mit
  request-lokalem KV-Cache, RNG, Timings, Abbruch und WAV-Bytes.
- Phase 2 abgeschlossen: Lazy-Load-Engine, Load-/Generate-Locks,
  Queuegrenze, Timeouts, Contracts, `/tts/health`, binaeres
  `/tts/generate` und der TTS-Capability-Block in `/health`.
- Phase 3 abgeschlossen: Der Port-8040-Launcher validiert und aktiviert den
  CPU-TTS-Pfad, VS-Code-Tasks decken Health und Generate-Smoke ab, und
  strukturierte Request-Logs enthalten Timings ohne Prompt- oder Audiodaten.
- Der reale CPU-Integrationstest erzeugt 25 Audiocodes als gueltige
  PCM16-WAV mit 12.800 Frames bei 24 kHz.
- Der Live-Service-Smoke erzeugt ueber `/tts/generate` eine gueltige WAV und
  wechselt den Lifecycle von `unloaded` auf `ready`.
- Der Phase-4-Spike wurde ausgefuehrt, der NPU Static-Cache Gate aber nicht
  bestanden. Ein fester Cache lief 100 Schritte mit genau einem Compile pro
  Device, erreichte jedoch nur 90,75 Prozent Top-1-Uebereinstimmung gegen CPU
  (363/400), maximalen Logitfehler 1,2677 und war mit 5,12 s langsamer als CPU
  mit 4,64 s. Das NPU-Plugin akzeptiert fuer diesen Graph nur f16 oder i8,
  nicht f32. Der produktive Modus bleibt daher `cpu_reference`.
- Phase 5 bleibt bis zu einem neuen numerisch und leistungsmessbar besseren
  NPU-Ansatz blockiert.
- Phase 6 abgeschlossen: `TtsServiceAdapter` kapselt Health, binaere
  Generierung, Fehlervertrag und WAV-/Headervalidierung. `generate_artifact`
  persistiert Audio sessiongebunden unter `.liara_artifacts/<session>/` und
  liefert ein `ChatArtifact` mit kontrollierter `/files/artifact`-Referenz.
  Der Live-Adaptertest gegen Port 8040 ist bestanden.
- Die Haupt-API stellt `GET /speech/health` und `POST /speech/generate` als
  kontrollierte Adaptergrenze bereit. Browser und andere Clients greifen
  nicht direkt auf Port 8040 zu. Transportfehler werden deterministisch als
  `tts_unavailable` (503) oder `tts_timeout` (504) gemeldet; strukturierte
  Servicefehler wie `tts_queue_full` behalten Statuscode und Retry-Hinweis.
- Der Task `liara-tts-api-proxy-smoke` prueft den realen Pfad
  `8010 -> 8040 -> /files/artifact` inklusive sessiongebundener URL,
  fehlendem Base64, MIME-Typ, PCM16-WAV und Metadatenkonsistenz. Der
  Referenzlauf erzeugte 12.800 Frames bei 24 kHz und 533 ms Dauer.
- Der deterministische `SpeechPlanner` liest Liaras kanonische
  `VoiceIdentity`, entfernt technisches Markup und erzeugt einen typisierten
  `SpeechPlan`. Satz, Liste, Zitat, Frage, Ausruf und Absatzgrenze erhalten
  unterschiedliche Pausen- und Prosodiehinweise. Die Engine setzt die
  geplanten Pausen bereits in PCM um; technische Cap-Teilungen verwenden eine
  kurze 80-ms-Pause und behalten Rolle sowie Prosodie des Ursprungsegments.
  Modellseitige Steuerung von Tempo, Ton und Betonung ist im aktuellen
  MiniCPM-o-Runtimepfad noch nicht implementiert und bleibt getrennte Arbeit.
- Port 8040 ist als `openvino_npu` in `service_guard` und im Desktop Server
  Management eingetragen. Start und Neustart verwenden weiterhin
  `scripts/start_openvino_npu_instance.ps1`, damit oneAPI, OpenVINO GenAI,
  Manifestpruefung und Sprecherprofil vor Uvicorn geladen werden. Der Guard
  wartet beim Stop auf Prozessende und Portfreigabe, bevor ein Neustart
  zugelassen wird.
- `speech.generate` wird nicht automatisch in die oeffentliche Tool-Registry
  aufgenommen. Orchestrator, Worker oder Coworker muessen die Faehigkeit
  explizit ueber den Adapter und mit Sessionkontext anfordern; Devicewahl und
  Modellbesitz bleiben im Service.
- Phase 7A abgeschlossen: Ein gemeinsamer, segmentweiser PCM16-Produzent ist
  die Quelle fuer den bestehenden WAV-Artefaktpfad und fuer Streaming. Die
  Engine erzeugt nur auf Nachfrage das naechste semantische Segment, gibt
  geplante Pausen als geordnete PCM-Frames aus und propagiert Abbruch bis in
  den laufenden Runtime-Loop.
- Port 8040 stellt `POST /tts/stream`, die Haupt-API kontrolliert
  `POST /speech/stream` bereit. Der binaere v1-Transport ist mono PCM16 little
  endian bei 24 kHz (`audio/x-pcm`) mit `audio_stream/v1`-Headern. Der
  bestehende `/tts/generate`- und AudioArtifact-Pfad bleibt kompatibel und
  sammelt denselben PCM-Produzenten als WAV ein.
- Der Live-Smoke `8010 -> 8040` lieferte 7.004 ms Audio und 336.192 PCM-Bytes.
  Zeit bis zu den ersten Bytes: 37.436 ms kalt inklusive Modellload und
  8.266 ms warm; warmer Gesamtlauf 19.696 ms. Der anschliessende WAV-
  Kompatibilitaetssmoke erzeugte 96.256 Frames, 4.011 ms Audio und HTTP 200.
  Nach dem finalen Serviceneustart mit warmem Host-Dateicache lag der erste
  Byte bei 9.610 ms und der Gesamtlauf bei 17.468 ms.

## Entscheidung

Audio und TTS werden als Servicefaehigkeiten des bestehenden eigenstaendigen OpenVINO-Inferenzdienstes auf Port 8040 eingeordnet.

```text
Worker / Coworker / Orchestrator
  -> typed HTTP adapter
  -> OpenVINO inference service :8040
       -> text/helper engine
       -> MiniCPM-o TTS engine
       -> spaeter: audio-understanding engine
```

Es wird kein zweiter TTS-Prozess auf einem neuen Port eingefuehrt, solange dieselbe NPU und dasselbe Modellbundle verwendet werden.

Die interne Implementierung bleibt modular. TTS wird nicht direkt in die Route oder in `OpenVINOProvider` eingebaut, sondern erhaelt eine eigene Engine und Runtime unter `services/inference/minicpmo_tts/`.

## Begruendung

Port 8040 und der OpenVINO-Prozess existieren bereits. Ein zweiter Prozess wuerde:

- Modellgewichte und Tokenizer doppelt laden,
- NPU-Speicher und Compile-Cache duplizieren,
- ohne zentrale Serialisierung mit Textinferenz um die NPU konkurrieren,
- Health, Start/Stop und Fehlerdiagnose auf zwei Dienste verteilen.

Die gemeinsame Prozessgrenze bedeutet nicht gemeinsame Fachlogik. Textinferenz und TTS behalten getrennte Engines, Locks, Health-Zustaende und Contracts.

## Einordnung in DDNA und Genome Cockpit

Die kanonische Trennung zwischen Genome Cockpit und technischer Architecture
Map steht in `docs/01_architektur/liara-ddna.md`. Dieses ADR entscheidet ueber
die technische Service- und Prozessgrenze. Die DDNA-Erweiterung selbst wird im
Cockpit-Katalog festgehalten.

Die Ebenen werden nicht vermischt:

```text
DDNA
  = konzeptuelle Identitaet und Beziehungsordnung von LIARA

Genome Cockpit
  = Navigations- und Inspektionsinstrument fuer diese DDNA

Architecture Map
  = strukturelle Abbildung konkreter Komponenten und Implementierungspfade

Runtime / Operations
  = aktueller Zustand, Health, Device Placement, Queue und Metriken
```

MiniCPM-o Audio/TTS erzeugt keinen zusaetzlichen Serviceprozess. Es ist ein
technisches Expression Binding fuer die DDNA-Faehigkeit `Speech`. Vision und
Audio Understanding koennen als weitere Engines desselben Services betrieben
werden, ohne dadurch mit `Speech` zu einer einzigen Faehigkeit zu verschmelzen:

```text
Genome Cockpit
C-GENE: Components
  -> models  (bestehend; Text/Inference)
  -> vision  (neu)
  -> hearing (neu)
  -> speech  (neu)
       -> Expression Binding: MiniCPM-o / OpenVINO
            -> Architecture Map: Inference
                 -> Runtime-Instanz: OpenVINO-Service auf Port 8040
```

`TTS` ist die technische Methode der Faehigkeit `Speech`. `Hearing` bezeichnet
die externe Audio-Wahrnehmung und ist nicht mit der internen Perception des
Self Observers gleichzusetzen: Der Self Observer beobachtet LIARAs eigenen
Zustand, waehrend Hearing externe Inhalte verarbeitet.

Die Ausgabegrenze ist formatunabhaengig modelliert: `SpeechPlan` verbindet die
DDNA-`VoiceIdentity` mit der technischen Expression; `AudioArtifact` beschreibt
persistente Ausgaben und `AudioStream` zukuenftige, geordnete und abbrechbare
Streams. WAV bleibt im aktuellen Pfad ein kompatibles Artefaktformat, ist aber
weder Teil der DDNA noch das kanonische interne Audioformat.

Darstellungskonsequenzen:

- Die sechs Primary Genes des Cockpits bleiben als uebergeordnete Familien
  bestehen.
- Der Cockpit-Katalog wird von 12 auf 15 Gene erweitert: `vision`, `hearing`
  und `speech` kommen unter `C-GENE: Components` hinzu.
- Die DDNA-Relationen des Cockpits werden fuer die drei neuen Gene explizit
  erweitert; sie sind nicht mit den technischen Kanten der Architecture Map
  gleichzusetzen.
- Im Genome Cockpit werden Gen und technische Expression unterscheidbar
  dargestellt.
- Aktivierungszustand, CPU/NPU-Zuteilung, Queue, Latenz und Fehler erscheinen nicht im Genome Cockpit, sondern in Runtime und Operations.
- Die Architecture Map behaelt einen gemeinsamen `Inference`-Knoten und darf
  ihn ueber `expressed_by` mit mehreren Faehigkeiten verbinden.
- Ein separater `TTS`-Serviceknoten waere erst gerechtfertigt, wenn Speech eine
  eigenstaendige Prozess-, Skalierungs- oder Governancegrenze erhaelt. Das ist
  mit dieser Entscheidung ausdruecklich nicht der Fall.

Die Landing Experience darf Speech als Faehigkeit LIARAs vermitteln. Sie soll
weder einen technischen TTS-Service als Primaersignal noch Service-Health oder
NPU-Metriken vor der DDNA-Orientierung zeigen.

## Verifizierter Ausgangspunkt

Folgendes ist belegt:

- Alle Audio/TTS-IRs laden mit OpenVINO.
- Der CPU-End-to-End-Lauf erzeugt aus 100 Audiocodes eine gueltige WAV mit 51.200 Samples bei 24 kHz.
- Audiofrontend, TTS-Adapter, TTS-Transformer und DVAE kompilieren fuer jeweils getestete statische Profile auf Intel NPU.
- Vocos kompiliert nicht auf NPU und muss explizit auf CPU laufen.
- Die LIARA-venv enthaelt OpenVINO, Transformers, Tokenizers und NumPy; Torch ist fuer den IR-Runtimepfad nicht erforderlich.
- `openvino_genai` wird im bestehenden Startskript ueber `OPENVINO_GENAI_PYTHON_DIR` eingebunden.

Nicht belegt ist ein kompletter TTS-Lauf mit dynamisch wachsendem KV-Cache auf NPU. Der bisherige NPU-Test deckt nur ein statisches Cacheprofil ab.

## Harte Architekturgrenzen

### 1. CPU-Referenz vor NPU-Optimierung

Service-v1 verwendet den vollstaendig validierten CPU-TTS-Pfad:

```text
TTS text/speaker embeddings -> CPU
TTS transformer             -> CPU
DVAE                         -> CPU
Vocos                        -> CPU
```

Dies ist das funktionale Referenzsystem fuer API, Concurrency, Abbruch, Limits und Regressionstests.

NPU wird erst aktiviert, wenn das Static-Cache-Gate bestanden ist. Teilweise NPU-Kompilierbarkeit allein reicht nicht.

### 2. Vocos bleibt CPU

Auch im spaeteren Mixed-Device-Modus gilt:

```text
NPU: TTS embeddings, TTS transformer, DVAE
CPU: Vocos ISTFT
```

`HETERO:NPU,CPU` wird fuer Vocos nicht verwendet. Der aktuelle NPU-Compiler akzeptiert die Operation zunaechst und scheitert erst spaet mit `Attribute strides can't be processed`, sodass HETERO nicht verlaesslich ausweicht.

### 3. Kein LLM-Duplikat fuer die Defaultstimme

Der Service-MVP laedt nicht bei jeder TTS-Anfrage den 3,8-GB-INT4-Sprachgraphen, nur um einen neutralen Sprecherzustand zu erzeugen.

Stattdessen wird ein versioniertes neutrales Sprecherprofil offline aus dem auditierten INT4-LLM erzeugt:

```text
tts/speakers/neutral-v1.npy       [1, 1, 3584], float32
tts/speakers/neutral-v1.json      Prompt, Tokenposition, Modellhash, Erzeugungsdatum
```

Der Hash des Quell-Language-Models und der Sprecherdatei gehoert in die Metadaten. Dynamische Sprecherableitung und Voice Cloning sind spaetere, getrennte Faehigkeiten.

### 4. Binaerausgabe bleibt binaer

WAV wird als `audio/wav` zurueckgegeben, nicht als Base64 in JSON. Metadaten werden in Response-Headern und strukturierten Logs gefuehrt.

### 5. Keine Client-seitige Device-Wahl

Worker und Coworker geben kein `device_override` an. Device Placement ist eine Betreiberentscheidung des Services.

## Zielstruktur

```text
services/inference/
├─ openvino_npu_app.py                 bestehender Prozess und HTTP-Oberflaeche
├─ providers/
└─ minicpmo_tts/
   ├─ __init__.py
   ├─ config.py                        validierte Konfiguration aus Environment
   ├─ engine.py                        Lifecycle, lazy load, Lock, Generate API
   ├─ runtime.py                       tensorreiner TTS-Loop, keine FastAPI-Abhaengigkeit
   ├─ artifacts.py                     Pfad-, Shape- und Hashvalidierung
   ├─ audio.py                         PCM16/WAV-Kodierung in Bytes
   └─ metrics.py                       Laufzeit- und Fehlerstatistik

services/contracts/service_boundaries.py
services/inference/tts_adapter.py       spaeterer Remote-Client fuer API/Orchestrator
tests/unit/test_minicpmo_tts_*.py
tests/integration/test_openvino_tts_cpu.py
tests/integration/test_openvino_tts_live.py
```

Die Exportscripte unter `scripts/` bleiben Build-/Auditwerkzeuge. Produktionscode importiert nicht aus `scripts/run_minicpmo_openvino_tts.py` und nicht aus `scripts/validate_openvino_llm_hidden_state.py`.

## API-Vertrag v1

### `GET /tts/health`

Antwort als JSON:

```json
{
  "status": "disabled|unloaded|loading|ready|degraded|failed",
  "backend": "minicpmo-openvino",
  "mode": "cpu_reference|mixed_npu_cpu",
  "devices": {
    "transformer": "CPU",
    "dvae": "CPU",
    "vocos": "CPU"
  },
  "model_dir": "C:/ai/models/OpenVINO/MiniCPM-o-2.6-int4-sym-cw-ov",
  "speaker_profile": "neutral-v1",
  "loaded": false,
  "queue_depth": 0,
  "request_count": 0,
  "failure_count": 0,
  "last_error": null
}
```

`GET /health` bleibt rueckwaertskompatibel und erhaelt nur einen zusaetzlichen `capabilities.tts`-Block. Ein Health-Aufruf laedt keine Modelle.

### `POST /tts/generate`

Request:

```json
{
  "text": "Hallo aus LIARA.",
  "speaker_profile": "neutral-v1",
  "max_audio_tokens": 100,
  "seed": 2606
}
```

Grenzen:

- `text`: 1 bis konfigurierbare Maximalzahl Zeichen, initial 2.000
- `speaker_profile`: Allowlist, initial nur `neutral-v1`
- `max_audio_tokens`: 25 bis 400, initialer Default 100
- `seed`: optionaler 32-Bit-Integer

Erfolg:

```text
HTTP 200
Content-Type: audio/wav
X-Liara-TTS-Request-Id: <uuid>
X-Liara-TTS-Audio-Tokens: 100
X-Liara-TTS-Sample-Rate: 24000
X-Liara-TTS-Duration-Ms: 2133
X-Liara-TTS-Mode: cpu_reference
Server-Timing: load;dur=..., generate;dur=..., vocos;dur=...
```

Fehler:

- `400`: ungueltiger Request
- `409`: Sprecherprofil passt nicht zum Modellbundle
- `429`: Queue voll
- `503`: Engine disabled, loading oder failed
- `504`: Generationsbudget ueberschritten

Fehlerantworten sind JSON und enthalten `request_id`, `code`, `message` und retry-faehige Metadaten.

### PCM-Streaming v1

`POST /tts/stream` liefert geordnete binaere PCM16-Frames. WAV wird nicht als
Zwischenformat verwendet. Der bestehende WAV-Endpunkt konsumiert denselben
PCM-Produzenten und setzt erst am Ende den Containerheader.

Der Stream besitzt folgende ausgehandelte Header:

```text
X-Liara-TTS-Stream-Contract: audio_stream/v1
X-Liara-TTS-Codec: pcm_s16le
X-Liara-TTS-Sample-Rate: 24000
X-Liara-TTS-Channels: 1
X-Liara-TTS-Mode: cpu_reference
```

HTTP erhaelt Byte-Reihenfolge und Backpressure. Ein Client-Disconnect schliesst
den Async-Iterator, setzt das kooperative Abbruchsignal und verhindert die
Erzeugung noch nicht angeforderter Segmente. Streamfehler nach dem Senden der
Response-Header beenden den binaeren Stream; strukturierte JSON-Fehler sind nur
vor Streambeginn moeglich.

Die kontrollierte 8010-Grenze setzt auf diesem PCM-Vertrag auf und bietet in
Phase 7B WebM/Opus als Default, Ogg/Opus und rohes PCM16 als Alternativen.
Der Encoder arbeitet progressiv und mit Low-Latency-Muxing. Ein optionaler
Tee schreibt dieselben PCM-Frames in eine `.wav.part`-Datei und committed sie
nur nach vollstaendig erfolgreichem Stream atomar als WAV-Artefakt.

## Contracts

In `services/contracts/service_boundaries.py` werden mindestens definiert:

- `TtsGenerationRequest`
- `TtsErrorResponse`
- `TtsHealthResponse`
- `TtsDevicePlacement`

Die erfolgreiche Audioantwort selbst ist binaer und wird nicht in ein Pydantic-Base64-Modell gezwungen.

Der spaetere API-/Orchestrator-Zugriff erfolgt ausschliesslich ueber `TtsServiceAdapter`. TTS wird nicht als `InferenceProvider` modelliert, weil `InferenceResult.content: str` und der bestehende Providervertrag semantisch Textinferenz beschreibt.

## Engine-Lifecycle

### Lazy Load

Startup prueft nur Konfiguration, Artefakte und Hashes. Modelle werden beim ersten expliziten Warmup oder Generate geladen.

```text
unloaded -> loading -> ready
                  \-> failed
ready -> degraded bei Runtimefehler/Fallback
```

Ein `asyncio.Lock` schuetzt den einmaligen Load. Die synchrone OpenVINO-Arbeit laeuft ueber `asyncio.to_thread`.

### Concurrency

Initial gilt genau eine aktive TTS-Generierung pro Prozess:

- globaler Generation-Lock fuer den TTS-Enginepfad,
- Request-lokaler KV-Cache und RNG,
- begrenzte Queue, initial maximal zwei wartende Requests,
- keine gemeinsam mutierten Infer-Requests zwischen Anfragen.

Text-/Helper-Inferenz und TTS muessen zusaetzlich ueber einen gemeinsamen Device-Arbiter serialisiert werden, bevor TTS NPU verwendet. Zwei unabhaengige Locks reichen fuer eine gemeinsam genutzte NPU nicht.

### Abbruch und Budgets

Der Runtime-Loop erhaelt ein kooperatives Abbruchsignal und prueft es mindestens pro Audiotoken. Limits:

- maximale Audiotokens,
- maximale Laufzeit,
- maximale Queuewartezeit,
- maximale Textlaenge.

Temporare WAV-Dateien sind im HTTP-Pfad nicht erforderlich. PCM16/WAV wird in `io.BytesIO` erzeugt.

## Konfiguration

Vorgesehene Environment-Variablen:

```text
OPENVINO_TTS_ENABLED=false
OPENVINO_TTS_MODEL_DIR=C:/ai/models/OpenVINO/MiniCPM-o-2.6-int4-sym-cw-ov
OPENVINO_TTS_SOURCE_DIR=C:/ai/models/OpenVINO/MiniCPM-o-2.6
OPENVINO_TTS_MODE=cpu_reference
OPENVINO_TTS_SPEAKER_PROFILE=neutral-v1
OPENVINO_TTS_MAX_TEXT_CHARS=2000
OPENVINO_TTS_MAX_AUDIO_TOKENS=400
OPENVINO_TTS_REQUEST_TIMEOUT_SECONDS=300
LIARA_TTS_TIMEOUT_SECONDS=360
OPENVINO_TTS_QUEUE_TIMEOUT_SECONDS=30
OPENVINO_TTS_MAX_QUEUE_DEPTH=2
OPENVINO_TTS_CPU_THREADS=<optional>
OPENVINO_TTS_CACHE_DIR=C:/ai/cache/openvino/minicpmo-tts
```

`OPENVINO_TTS_SOURCE_DIR` ist nur in der Uebergangsphase fuer den ChatTTS-Tokenizer erforderlich. Vor Produktionsfreigabe soll das audierte Bundle selbsttragend werden:

- ChatTTS-Tokenizer in das Bundle kopieren,
- neutrales Sprecherprofil in das Bundle schreiben,
- alle Runtimepfade relativ zum Bundle aufloesen.

## NPU Static-Cache Gate

Vor `OPENVINO_TTS_MODE=mixed_npu_cpu` ist ein eigener technischer Spike verpflichtend.

Ziel ist ein TTS-Transformergraph mit festen Maximalformen und expliziter Cacheposition, nicht ein Satz von hunderten pro Cachelange kompilierten Graphen.

Zu pruefen:

1. Fester KV-Cache `[1, 12, MAX_CACHE, 64]` pro Layer.
2. `cache_position` bestimmt Schreib- und Leseposition.
3. Ausgabe bleibt feste Cacheform oder liefert nur den neuen KV-Slice.
4. Prefill und Decode koennen getrennte statische Graphen sein.
5. Mindestens 100 autoregressive Schritte laufen ohne Recompile.
6. Audiocode-Top-1-Uebereinstimmung gegen FP32-CPU-Referenz ist 100% fuer den deterministischen Test; fuer Sampling werden identische Logits-Toleranzen und Seeds verglichen.
7. Eine komplette Mixed-Device-WAV besteht Header-, Finite-, RMS- und Nichtstille-Pruefung.
8. Textinferenz auf derselben NPU bleibt nach TTS nutzbar.

Wenn dieses Gate scheitert, bleibt der Service im `cpu_reference`-Modus. Ein Profil pro Tokenposition ist keine akzeptierte Produktionsloesung.

## Implementierungsphasen

### Phase 0: Artefaktvertrag und Sprecherprofil

- Runtime-Manifest fuer alle benoetigten IRs, Ports, Shapes, Dtypes und SHA-256 erstellen.
- `neutral-v1.npy` aus dem auditierten INT4-LLM erzeugen und numerisch gegen Live-Extraktion pruefen.
- ChatTTS-Tokenizer in das Modellbundle kopieren.
- Loader validiert Vollstaendigkeit und Modell-/Sprecherhashes.

Abnahme:

- Bundle funktioniert ohne Original-Checkpointpfad.
- Keine Torch-Abhaengigkeit im Runtimeprozess.
- Manipulierte oder fehlende Artefakte fuehren zu `failed`, nicht zu einem spaeten Inferfehler.

### Phase 1: Reine Runtime-Bibliothek

- Logik aus `run_minicpmo_openvino_tts.py` nach `runtime.py` uebertragen.
- Konstanten aus validiertem Runtime-Manifest lesen.
- Waveform als NumPy und WAV als Bytes liefern.
- Request-lokalen Cache, RNG, Timings und kooperativen Abbruch implementieren.

Abnahme:

- Deterministischer 25- und 100-Code-Test stimmt mit dem auditierten Scriptlauf ueberein.
- Kein Import aus `scripts/`.
- Unit-Tests koennen CompiledModel-Aufrufe faken.

### Phase 2: CPU-Engine und Servicevertrag

- `OpenVINOTtsEngine` mit lazy load, Load-Lock, Generate-Lock und Queuegrenze bauen.
- Contracts ergaenzen.
- `/tts/health` und `/tts/generate` in den bestehenden Port-8040-App einhaengen.
- Bestehendes `/infer` und `/infer/helper` unveraendert lassen.

Abnahme:

- Bestehende OpenVINO-NPU-Smokes bleiben gruen.
- Zwei parallele TTS-Anfragen vermischen weder Cache noch Seed.
- Dritte Anfrage wird gemaess Queuepolicy abgelehnt oder wartet begrenzt.
- WAV-Antwort ist ohne temporaere Datei gueltig.

### Phase 3: Betriebsintegration

- Bestehendes `start_openvino_npu_instance.ps1` um TTS-Environmentvalidierung erweitern.
- Neue VS-Code-Tasks fuer TTS Health und CPU Generate Smoke ergaenzen.
- Den OpenVINO-Service erst dann in `service_guard.py` aufnehmen, wenn der Guard Launcher-Scripte mit oneAPI/OpenVINO-GenAI-Environment korrekt unterstuetzt. Ein normaler uvicorn-Start ueber `_build_command` reicht aktuell nicht.
- Strukturierte Logs und Runtime-Metriken ergaenzen.

Abnahme:

- Start, Health, Generate, Stop und Neustart sind reproduzierbar.
- Health loest keinen Modellload aus.
- Fehler enthalten Request-ID und keine internen Pfade oder Stacktraces im HTTP-Body.

### Phase 4: NPU Static-Cache Spike

- Statischen Prefill-/Decode-Graph prototypisieren.
- Numerik und 100-Schritt-Loop gegen CPU-Referenz validieren.
- Gemeinsamen Device-Arbiter fuer Text und TTS entwerfen.

Abnahme:

- Alle Kriterien des NPU Static-Cache Gate sind erfuellt.

### Phase 5: Mixed-Device-Aktivierung

- NPU fuer Embeddings, Transformer und DVAE aktivieren.
- Vocos explizit CPU belassen.
- Warmup, Compile-Cache und Fallbackpolicy implementieren.
- Live-Regression und Lasttest ausfuehren.

Abnahme:

- Komplette WAV im Mixed-Device-Modus.
- Keine Recompilierung pro Audiotoken.
- Keine Regression fuer `/infer` und `/infer/helper`.
- CPU-Fallback ist explizit sichtbar und nicht still.

### Phase 6: Adapter fuer Worker/Coworker

- `TtsServiceAdapter` als Remote-Client einfuehren.
- Orchestrator entscheidet ueber Nutzung; Worker/Coworker besitzen keine Modelle und keine Devicewahl.
- Audio als `ChatArtifact` oder dedizierter Downloadverweis weiterreichen, nicht als Textinhalt.

Abnahme:

- Rollenlogik bleibt ausserhalb des TTS-Service.
- Derselbe Servicevertrag funktioniert fuer Worker, Coworker und UI.

### Phase 7A: Segmentweiser PCM-Stream

- Die Engine stellt einen gemeinsamen, geordneten PCM-Produzenten bereit.
- `/tts/generate` sammelt ihn weiterhin als kompatible PCM16-WAV.
- `/tts/stream` und `/speech/stream` reichen binaere PCM-Frames mit
  Backpressure weiter.
- Disconnect und Task-Cancellation setzen das request-lokale Abbruchsignal.

Abnahme:

- Semantische Segmente und geplante Pausen erscheinen in stabiler Reihenfolge.
- Ohne Konsum wird kein weiteres Segment erzeugt.
- Abbruch erreicht einen bereits laufenden Backend-Aufruf.
- Der bestehende WAV-/AudioArtifact-Smoke bleibt gruen.

### Phase 7B: Browsercodec und persistenter Tee

- WebM/Opus ist als bevorzugter Browsertransport und Ogg/Opus als Alternative
  hinter den PCM-Produzenten gesetzt.
- Deterministische 8-ms-Raised-Cosine-Fades werden vor der PCM-Kodierung an
  Segmentkanten angewendet; Samplezahl und Pausenplanung bleiben unveraendert.
- Der Stream kann optional parallel und ohne Base64 als WAV-Artefakt
  persistiert werden. Abbruch und Fehler entfernen die partielle Datei.
- Das Next.js-Webfrontend konsumiert den Response-Stream progressiv ueber
  `MediaSource`; sein Stop-Signal bricht den HTTP-Request ab. Browser ohne
  `MediaSource` verwenden automatisch den kompatiblen WAV-Artefaktpfad.
- Der bestehende `/speech/generate`-WAV-Pfad bleibt kompatibel.

Offen bleiben die Aushandlung von Codec und Transport ueber LiNeP, eine
subjektive Hoerqualifizierung/Browsermatrix und gegebenenfalls echte
Crossfades mit ueberlappendem Prosodiekontext. LiNeP erhaelt dabei weiterhin
keine Voice Identity.

## Testmatrix

### Unit

- Contract-Grenzen und ungueltige Werte
- Artefaktmanifest und Hashfehler
- Load nur einmal bei parallelen Erstzugriffen
- Request-lokaler KV-Cache und RNG
- Queue voll, Queue-Timeout, Generate-Timeout
- WAV-Header und PCM-Clipping
- Health-Zustandsuebergaenge
- Fehlerredaktion
- PCM-Reihenfolge, Pausenframes und Backpressure
- Cancellation waehrend eines laufenden Backend-Aufrufs
- Streamheader und 8010-Proxy

### CPU Integration

- 25- und 100-Code-Generierung
- deterministischer Seed
- zwei sequenzielle unterschiedliche Texte
- parallele Requests ohne Cache-Leak
- Prozess-RSS vor/nach wiederholten Requests
- bestehende `/infer`- und `/infer/helper`-Tests

### NPU Live

- Static-Cache 100 Schritte ohne Recompile
- Mixed NPU/CPU WAV
- Text -> TTS -> Text auf derselben NPU
- Neustart mit warmem Compile-Cache
- NPU nicht vorhanden: sichtbarer CPU-Fallback oder Service `degraded`, je nach Policy

## Observability

Mindestens zu erfassen:

- Request-ID
- Queuewartezeit
- Load-/Compilezeit
- Zeit bis erster Audiocode
- Audiocodes pro Sekunde
- DVAE- und Vocos-Zeit
- Gesamtlatenz
- Ausgabe-Samples und Dauer
- aktiver Modus und effektive Devices
- Abbruch-, Timeout- und Fehlerzaehler
- letzter Fehler als redigierter Code

Prompts und erzeugte Audiodaten werden standardmaessig nicht in Logs persistiert.

## Verbleibende offene Entscheidungen

Keine davon blockiert Phase 0 und 1:

- Soll `/tts/generate` intern oder ueber den API-Service nach aussen exponiert werden? Empfehlung: Port 8040 bleibt intern; die oeffentliche API liefert spaeter einen kontrollierten Proxy/Artifact-Link.
- Soll v1 nur Deutsch/Englisch erlauben oder sprachagnostisch bleiben? Empfehlung: sprachagnostischer Textvertrag, aber auditiertes Qualitaetslabel nur fuer getestete Sprachen.
- Soll bei NPU-Ausfall automatisch CPU genutzt werden? Empfehlung: fuer TTS ja, aber als `degraded` und im Response-Header sichtbar.

## Nicht Teil dieses Plans

- Voice Cloning
- Upload fremder Sprecher-Audios
- Audio Understanding / ASR-Endpunkt
- Echtzeit-WAV-Streaming
- TTS-INT4-Quantisierung
- subjektive Sprachqualitaetsfreigabe

Diese Punkte erhalten eigene Entscheidungen, nachdem der CPU-Referenzservice stabil ist.

## Leitsatz

```text
TTS ist eine Servicefaehigkeit.
Worker und Coworker nutzen sie, besitzen sie aber nicht.
CPU ist die Referenz; NPU ist eine nachzuweisende Optimierung.
```
