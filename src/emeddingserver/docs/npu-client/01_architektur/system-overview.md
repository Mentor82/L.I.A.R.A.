# Liara System Overview

## Ziel

Dieses System beschreibt eine skalierbare Architektur fuer:

- verteilte Inference-Helper (NPU/OpenVINO)
- deterministische Task-Verteilung
- klare Task-Routing-Regeln
- minimalen Netzwerk-Overhead
- modulare Erweiterbarkeit ueber DLLs (Windows C++)

## Gesamtarchitektur

```text
User
 ↓
Liara API
 ↓
Orchestrator / Scheduler
 ↓
 ├─ NPU Helper (LiaraHelperInferServer)
 │     ├─ LoadLibrary(liara_instruct.dll)  -> Instruct, warm
 │     └─ LoadLibrary(liara_coder.dll)     -> Coder, optional
 ├─ Scheduler Routing Layer (LiaraHelperScheduler)
 └─ Probe + Readiness Gate (openvino_probe)
```

## Verzeichnisstruktur

```text
Liara-NPU-Client/
  liara-helper/            <- Helper-Server + Pipeline-DLLs
    LiaraHelperInferServer.exe
    plugins/
      liara_instruct.dll
      liara_coder.dll
      pipeline_dll.cpp     <- gemeinsame DLL-Quelle
  liara-scheduler/         <- Scheduler
    LiaraHelperScheduler.exe
    scheduler_main.cpp
  liara-inference/
    common/                <- geteilte Header (protocol.hpp, pipeline_plugin.hpp etc.)
    templates/
  src/                     <- Nebenkomponenten (heartbeat_demo, openvino_probe, embedding)
  runtime/                 <- OpenVINO Runtime DLLs (deployment)
  vendor/                  <- openvino_genai headers + lib
```

## Rollen

```text
Liara (Primaer)
  - entscheidet
  - kombiniert Ergebnisse
  - erzeugt finale Antwort

Helper (NPU / CPU)
  - laedt Instruct und Coder als DLLs (liara_instruct.dll, liara_coder.dll)
  - haelt beide Pipelines warm
  - routed quick_extract auf Instruct
  - routed code_* auf Coder (System-Prompt; Pipeline aktuell Instruct)
  - liefert Warm-Statusmetriken (warm_age_ms, reload_count)

Scheduler
  - verteilt Tasks
  - validiert Profil-Readiness und Warm-Readiness
  - nutzt dieselben Routing-Regeln wie der Helper
```

## Skalierung

```text
Aktueller Fokus ist die lokale Runtime-Basis:

- reproduzierbarer Build mit MSVC
- OpenVINO probe-basierte Device-Pruefung
- stabile In-Memory-Bereitstellung der zwei Kernprofile
```

## Leitsaetze

```text
Worker melden Zustand.
Scheduler entscheidet.
Helper inferieren.
Liara orchestriert.
```

```text
Binaer fuer Betrieb.
JSON fuer Diagnose.
DLL fuer Erweiterung.
Pipeline-DLL pro Modell.
```

```text
Explizites Device.
Explizites Routing.
Expliziter Warm-Status.
```

## Zielzustand

Ein verteiltes, robustes Inference-System mit:

- hoher Parallelitaet
- stabilen Ergebnissen
- geringer Bandbreite
- modularer Erweiterbarkeit
- klarer Trennung der Verantwortlichkeiten

Der aktuelle Ist-Zustand priorisiert korrekten Build, reproduzierbare Device-Pruefung und warm gehaltene Instruct/Coder-Modelle als stabile Grundlage fuer den naechsten Integrationsschritt.
