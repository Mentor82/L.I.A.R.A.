# 🚀 Liara – Distributed NPU Inference Helper System (Spec)

Hinweis: Diese Datei bleibt als konsolidierte Legacy-Spezifikation erhalten.
Strukturierte Teil-Dokumente befinden sich unter:

- `01_architektur/system-overview.md`
- `01_architektur/scheduler-consensus.md`
- `02_services/npu-helper-service.md`
- `02_services/plugin-host-service.md`
- `04_runbooks/openvino-worker-readiness.md`
- `08_security/security-principles.md`
- `09_reference/heartbeat-protocol.md`

---

## 🧭 Ziel

Dieses System beschreibt eine skalierbare Architektur für:

- verteilte Inference-Helper (NPU(OpenVino) / Edge(vorbereitung))
- deterministische Task-Verteilung
- Konsensbasierte Ergebnisvalidierung
- minimalen Netzwerk-Overhead
- modulare Erweiterbarkeit über DLLs (Windows C++)

---

## 🧱 Gesamtarchitektur

```text
User
 ↓
Liara API
 ↓
Orchestrator / Scheduler
 ↓
 ├─ NPU Helper Cluster (Instruct / Coder)
 ├─ Edge Inputs
 ├─ Consensus Gate
 └─ Primary Liara Model
```

---

## 🧠 Rollen

```text
Liara (Primär)
  - entscheidet
  - kombiniert Ergebnisse
  - erzeugt finale Antwort

Helper (NPU)
  - führt Inference aus
  - liefert Kandidaten

Edge
  - liefert Rohdaten / Voranalysen

Scheduler
  - verteilt Tasks
  - verwaltet Slots
  - kennt Redundanz
```

---

## ⚡ NPU Inference Helper

Jeder Helper ist:

```text
- Windows C++ Prozess
- OpenVINO Runtime
- 1–2 Modelle (z. B. instruct + coder)
- Netzwerkfähig (HTTP oder TCP)
- stateless
```

---

## 🧩 OpenVINO Libraries (Intel AI Boost / NPU)

### Ziel Heartbeat

- nur notwendige Runtime-Komponenten ausliefern
- NPU (Intel AI Boost) priorisieren
- CPU als Fallback erlauben

### Muss (für C++ Runtime)

```text
openvino.dll
openvino_c.dll (nur wenn C-API genutzt wird)
openvino_intel_npu_plugin.dll
openvino_intel_cpu_plugin.dll (Fallback)
tbb12.dll
tbbmalloc.dll
```

### Format-abhängig (nur wenn benötigt)

```text
openvino_onnx_frontend.dll  (wenn ONNX direkt geladen wird)
openvino_paddle_frontend.dll
openvino_tensorflow_frontend.dll
openvino_pytorch_frontend.dll
```

### Tokenizer / GenAI (nur bei LLM-Pipelines)

```text
openvino_tokenizers.dll
openvino_genai.dll
```

### Empfehlung für Liara

```text
Bevorzugtes Modellformat: OpenVINO IR (.xml + .bin)
-> reduziert Abhaengigkeit von Frontend-DLLs
-> stabilere Runtime-Verteilung auf Worker
```

### Pruefablauf (pro Worker-Image)

```text
1) core.get_available_devices() enthaelt NPU
2) einfaches IR-Modell auf NPU kompilierbar
3) CPU-Fallback kompilierbar
4) ONNX-Laden nur testen, wenn ONNX im Betrieb genutzt wird
5) Smoke-Test unter Last (Inference + Heartbeat parallel)
```

### Probe-Tool (empfohlen)

```text
openvino_probe <model.xml> --device=npu
openvino_probe <model.xml> --device=cpu
openvino_probe <model.xml> <model.onnx> --device=npu
openvino_probe <model.xml> --device=npu --runtime-dir=C:\deploy\openvino
openvino_probe <model.xml> --device=npu --max-load=35 --throttle-ms=250
openvino_probe <model.xml> --device=npu --profile=shared-notebook
openvino_probe <model.xml> --device=npu --profile=shared-notebook --max-load=25
```

```text
--runtime-dir erzwingt eine strikte DLL-Pruefung im angegebenen Verzeichnis.
Damit werden keine zufaellig verfuegbaren PATH-DLLs als false-positive gewertet.
--device ist Pflicht: entweder NPU oder CPU, niemals Auto-Auswahl.
--profile=shared-notebook setzt standardmaessig max-load=30 und throttle-ms=300.
--max-load begrenzt die Probe-Intensitaet (1..100), standard = 100.
--throttle-ms fuegt zwischen Testphasen eine Wartezeit ein, standard = 0.
Explizite Flags haben Vorrang vor Profil-Defaults.
```

---

## 📡 Heartbeat-Protokoll (binär)

### Ziel

- minimale Bandbreite
- schnelle Auswertung

### Paket (12 Byte)

```text
Magic      2 Byte
Version    1 Byte
Type       1 Byte
WorkerID   2 Byte
SlotID     1 Byte
Flags      1 Byte
Load       1 Byte
Queue      1 Byte
Seq        1 Byte
CRC8       1 Byte
```

---

## 🧠 Scheduler

### Ablauf Scheduler

```text
Task kommt rein
 → passende Slots filtern
 → harte Filter anwenden
 → Score berechnen
 → besten Slot wählen
 → Task senden
```

---

## 🔁 Triple-Inference Consensus Gate

### Ablauf Consensus

```text
Task
 → Helper A
 → Helper B
 → Helper C

Ergebnisse
 → Embedding
 → Similarity

wenn ≥2 Ergebnisse >= 0.99
 → akzeptieren
sonst
 → reject / retry
```

---

## ⚡ Fast-Fail

```text
2 Ergebnisse vorhanden
Similarity >= 0.99
→ sofort akzeptieren
→ drittes Ergebnis optional ignorieren
```

---

## 📊 Konsens-Level

```text
2/3 = akzeptiert
3/3 = high confidence
<2 = fail
```

---

## 🌐 Edge Integration

```text
Edge liefert:
- OCR
- Logs
- Status
- Analyse
```

### Pipeline

```text
Edge → Liara → Consensus → Liara Verarbeitung
```

---

## 🧱 DLL Plugin-System (Windows)

### Ziel DLL-System

- modulare Erweiterbarkeit
- keine Recompiles
- dynamisches Laden von Modellen / Helfern

---

### Grundprinzip

```text
LiaraHelper.exe
  ↓
lädt Plugins (*.dll)
  ↓
stellt Slots bereit
```

---

### DLL Interface

```cpp
extern "C" __declspec(dllexport)
bool liara_init();

extern "C" __declspec(dllexport)
bool liara_infer(const char* input, char* output);

extern "C" __declspec(dllexport)
void liara_shutdown();
```

---

### Erweiterte Variante (empfohlen)

```cpp
struct LiaraRequest {
    const char* input;
    int max_tokens;
    float temperature;
};

struct LiaraResponse {
    char* output;
    int status;
};

extern "C" __declspec(dllexport)
bool liara_infer(LiaraRequest* req, LiaraResponse* res);
```

---

### Plugin-Typen

```text
instruct.dll
coder.dll
classifier.dll
summarizer.dll
```

---

### Laden der DLLs

```cpp
HMODULE h = LoadLibrary("instruct.dll");
auto infer = (InferFunc)GetProcAddress(h, "liara_infer");
```

---

### Vorteil

```text
neue Modelle ohne Neustart
Feature-Flags möglich
Rollback einfach
```

---

## 🔐 Sicherheitsprinzip

```text
Helper:
  - keine eigene Logik
  - keine DB-Zugriffe
  - keine Entscheidungen

Scheduler:
  - einzige Entscheidungsinstanz

Liara:
  - einzige Interpretationsinstanz
```

---

## 📈 Skalierung

```text
50 Notebooks
2 Slots pro Gerät
= 100 Slots

Triple-Inference
→ 3 Slots pro Task

Parallelität bleibt hoch
```

---

## 🧠 Leitsätze

```text
Worker melden Zustand.
Scheduler entscheidet.
Helper inferieren.
Liara versteht.
```

```text
Binär für Betrieb.
JSON für Diagnose.
DLL für Erweiterung.
```

```text
Ein Ergebnis ist Meinung.
Zwei sind Hinweis.
Drei mit Konsens sind belastbar.
```

---

## 🏁 Zielzustand

Ein verteiltes, robustes Inference-System mit:

- hoher Parallelität
- stabilen Ergebnissen
- geringer Bandbreite
- modularer Erweiterbarkeit
- klarer Trennung der Verantwortlichkeiten
