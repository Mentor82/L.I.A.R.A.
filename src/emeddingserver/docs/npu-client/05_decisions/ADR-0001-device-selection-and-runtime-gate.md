# ADR-0001: Device Selection And Runtime Gate

## Status

Accepted

## Context

Liara-Worker laufen auf gemeinsam genutzten Notebooks mit Intel AI Boost (NPU), die parallel auch andere Aufgaben ausführen.

Bisherige Risiken:

- automatische Device-Wahl kann unvorhersehbare Last erzeugen
- fehlende Runtime-DLLs werden oft erst zur Laufzeit sichtbar
- unterschiedliche lokale PATH-Umgebungen führen zu false-positive Starts

Das System benötigt einen deterministischen und reproduzierbaren Betriebsmodus pro Worker.

## Decision

1. Device-Auswahl ist explizit und verpflichtend.

- Jeder Probe- und Startlauf muss genau ein Device festlegen: `NPU` oder `CPU`.
- Auto-Auswahl ist verboten.

1. Worker-Start hat ein Runtime-Gate.

- Vor Start muss `openvino_probe` erfolgreich sein (`Overall: PASS`).
- Bei `FAIL` wird der Worker nicht gestartet.

1. Runtime-DLL-Prüfung ist Teil des Gates.

- Pflicht-DLLs müssen vorhanden sein.
- Für reproduzierbare Deployments wird `--runtime-dir=<path>` bevorzugt.

1. Shared-Notebook-Profil ist standardisiert.

- `--profile=shared-notebook` setzt ressourcenschonende Defaults.
- Explizite Flags (`--max-load`, `--throttle-ms`) übersteuern Profilwerte.

## Consequences

Positive Auswirkungen:

- deterministisches Verhalten pro Node
- weniger Laufzeitfehler durch fehlende OpenVINO-Komponenten
- geringere Störung anderer Workloads auf geteilten Geräten
- klare Betriebsregel für Ops und Scheduling

Negative Auswirkungen / Kosten:

- zusätzlicher Startschritt (Probe)
- initialer Konfigurationsaufwand pro Deployment-Image

Verbleibende Risiken:

- NPU-Verfügbarkeit kann sich zur Laufzeit ändern (z. B. Treiberprobleme)
- Probe reduziert Risiko, ersetzt aber kein Monitoring im Betrieb

## Alternatives Considered

- Option A: Auto-Device Auswahl durch Runtime
  - verworfen wegen nicht-deterministischer Lastverteilung auf Shared-Hardware

- Option B: Start ohne Probe, Fehler erst im Betrieb behandeln
  - verworfen wegen hoher Ausfall-/Diagnosekosten

- Option C: Nur CPU erzwingen
  - verworfen, da Intel AI Boost (NPU) gezielt genutzt werden soll

## Operational Notes

Empfohlene Aufrufe:

- `openvino_probe <model.xml> --device=npu --runtime-dir=C:\deploy\openvino --profile=shared-notebook`
- `openvino_probe <model.xml> --device=cpu --runtime-dir=C:\deploy\openvino --max-load=25 --throttle-ms=400`

Referenzen:

- `docs/04_runbooks/openvino-worker-readiness.md`
- `docs/02_services/npu-helper-service.md`
- `docs/08_security/security-principles.md`
