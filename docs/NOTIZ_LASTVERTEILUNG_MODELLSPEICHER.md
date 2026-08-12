# Notiz: Lastverteilung und Modellspeicher (LLAMA)

## Kurzfassung

- Es wird dieselbe Modelldatei genutzt (`LLAMA_CPP_MODEL`), aber nicht derselbe geladene Speicherzustand zwischen Varianten.
- Die aktuelle Auswahl zwischen `sycl`, `vulkan`, `cpu` ist statisch/fallback-basiert, nicht dynamisch lastbasiert.
- Verhalten wirkt teilweise wie Lastverteilung, ist aber primär GPU-Offload + Ressourcenlimit (v. a. VRAM) bedingt.

## Ist-Zustand

### 1) Backend-Auswahl

- Variante wird über `LLAMA_CPP_BUILD_VARIANT` festgelegt (`auto` oder explizit).
- Bei `auto` wird in fester Reihenfolge ein vorhandener Build gewählt.
- Das ist kein Runtime-Scheduler mit Queue-/Load-Metriken.

### 2) Modellspeicher

- Alle Varianten zeigen auf dieselbe GGUF-Datei.
- Jede Variante läuft als eigener Prozess/Binary.
- Der geladene Modellzustand wird nicht zwischen Prozessen geteilt.
- Wechsel der Variante bedeutet erneutes Laden in RAM/VRAM.

### 3) GPU/CPU-Offload

- Start erfolgt mit hoher GPU-Offload-Intention (`-ngl 99` / `--n-gpu-layers 99`).
- Was nicht auf GPU passt oder vom Backend nicht getragen wird, läuft auf CPU.
- Dadurch entsteht der Eindruck einer Lastverteilung, tatsächlich ist es Offload-Fallback.

## Beobachtung aus Benchmarks

- `vulkan` war im Gesamtlauf am schnellsten.
- `cpu` war deutlich langsamer, trotz optimiertem CPU-Build.
- Der CPU-Abstand ist plausibel durch fehlenden GPU-Offload, nicht primär durch `f16c`.

## Konsequenzen

- Häufiges Start/Stop verschiedener Varianten erzeugt zusätzlichen Load durch Reload.
- Stabiler Betrieb profitiert von warmen, laufenden Instanzen statt Prozesswechsel pro Request.

## Spaetere Architekturfrage: globale Ressourcenverteilung

Beobachtung vom 2026-07-14 auf einem ThinkPad T16 Gen 3 mit Intel Core Ultra
7 155H: Unter gleichzeitiger Inferenz-, Embedding-, NPU-, WSL- und
Docker-Last sank der Akkustand trotz angeschlossenem Netzteil. Nach Ende der
Inferenz und Embeddings stieg er wieder. HWiNFO zeigte im ruhigeren Zustand
keine aktuelle thermische Drosselung, aber ein dauerhaft aktives
Package-Level-PL1-Limit bei dynamisch 13 W. Die Akkuladerate wurde damit zu
einem brauchbaren Rueckkopplungssignal fuer das reale Gesamtleistungsbudget.

Diese Beobachtung soll spaeter in einen globalen Resource Scheduler einfliessen.
Einzelne Auslastungswerte reichen nicht aus: freie CPU-Kapazitaet bedeutet
nicht automatisch freie Energie-, Speicher- oder Kuehlkapazitaet.

Zu beruecksichtigender Zustandsvektor:

```text
R = (CPU, GPU, NPU, RAM, power, thermal, battery_charge_rate)
```

Die spaetere Verteilung muss drei getrennte Fragen beantworten:

```text
Ressourcenbegrenzung -> Wie viel darf insgesamt verbraucht werden?
Ressourcenverteilung -> Welche Ressource erhaelt welche Aufgabe wann?
Admission Control    -> Darf der naechste Schritt jetzt starten?
```

Wichtige Signale sind neben CPU/GPU/NPU/RAM insbesondere PL1/PL2-Flags,
Thermal-Events, Systemleistung, Akkuladerate im Netzbetrieb, reservierter
Shared Memory sowie die P-/E-Core-Verteilung. Diese Notiz dokumentiert eine
spaetere Architekturfrage und legt noch keine konkrete Scheduler-Policy fest.

### Bezug zu LiNeP, Helper und Scheduler

Diese spaetere Steuerung gehoert in den bereits definierten nativen Pfad:

```text
Helper / Embedding Worker
  -> melden Capability, Readiness, Slot-Zustand und Ressourcenbedarf
  -> LiNeP transportiert Heartbeats, Telemetrie, Lease und Ergebnis
  -> Scheduler bewertet globale Kapazitaet und erteilt Admission/Placement
  -> Worker fuehrt nur den freigegebenen Auftrag aus
```

Die Verantwortlichkeiten bleiben getrennt:

```text
LiNeP      = native Transport- und Slot-Ebene
Scheduler  = Verteilung, Admission Control und Backpressure
Helper     = spezialisierte NPU-Ausfuehrung
Embedding  = spezialisierte Embedding-Ausfuehrung
Orchestrator = fachlicher Auftrag und Workflow, nicht Hardware-Taktgeber
```

Der vorhandene LiNeP-Heartbeat kann damit spaeter von reiner
Verfuegbarkeit um einen kompakten Ressourcenvektor erweitert werden. HWiNFO
ist fuer die aktuelle Beobachtung die Messquelle, aber nicht automatisch der
spaetere Produktions-Contract. Die kanonische Integrationsgrenze bleibt in
`docs/05_decisions/adr-002-cpp-openvino-embedding-primary.md` beschrieben.

## Empfehlung (pragmatisch)

1. Standardpfad: `vulkan`
2. Fallback: `sycl`, dann `cpu`
3. Backend-Prozesse warm halten (nicht pro Request neu starten)
4. Optional später: score-basiertes Routing mit Metriken (TTFT, queue_len, error_rate)

## Relevante Konfiguration

- `LLAMA_CPP_MODEL`
- `LLAMA_CPP_BUILD_VARIANT`
- `LLAMA_CPP_BASE_URL`
- `LLAMA_CPP_TIMEOUT_SECONDS`
- `DEFAULT_LLM_PROVIDER`

## Hinweis zur Stabilität (Windows/IntelLLVM)

- Bei IntelLLVM-basierten Binaries kann ein korrekt geladenes oneAPI-Environment für stabile Starts relevant sein.
