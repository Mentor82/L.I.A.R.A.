# Service: liara-tools

Stand: 2026-07-13  
Code: `services/tools/`

## Aufgabe

`liara-tools` stellt deterministische Funktionen bereit, die der Orchestrator oder API-Endpunkte ausfuehren koennen.

## Registry

Die globale Registry in `services/tools/registry.py` registriert aktuell:

| Tool | Klasse |
| --- | --- |
| `sys` | `WslExecutorTool` |
| `orientation` | `OrientationTool` |
| `compute.run` | `ComputeTool` |
| `compute.generate` | `ComputeGenerateTool` |
| `plot_chart` | `PlotChartTool` |
| `wsl_session` | `WslSessionTool` |

Oeffentliche Standardliste ueber `GET /tools`:

- `sys`
- `orientation`
- `plot_chart`
- `wsl_session`

Nicht-oeffentliche oder nicht mehr regulaer exposed Tool-Namen:

- `compute.run`
- `compute.generate`
- `read_file`
- `list_files`
- `web_search`

Historische Direkttools wie `fetch`, `current_time` und `session_context` gehoeren ebenfalls nicht mehr zur regulaeren public Tool-Flaeche.

## Koordination

`services/tools/coordinator.py` uebernimmt Toolausfuehrung und liefert `ToolExecutionResult`-Contracts zurueck.

## Sicherheitsbezug

Besonders relevante Tools:

- `sys`: braucht Policy-/Sandbox-Grenzen.
- `compute.run`: fuehrt allowlistete Julia-Modelle standardmaessig ueber die
  gehaertete Debian-WSL-Bridge aus (`JULIA_BRIDGE_MODE=wsl`). Der lokale Modus
  ist nur ein expliziter Development-Override.
- `compute.generate`: generiert Compute-Artefakte.
- `plot_chart`: erzeugt Visualisierungen/Artefakte.
- `wsl_session`: verwaltet isolierte native WSL-Snapshots und Kandidaten.

Judge- und Sys-Audit-Komponenten beobachten riskante Aktionen.

## WSL-Session-Lifecycle

`wsl_session` bietet die Aktionen:

- `plan`: Umfang eines gefilterten Snapshots pruefen
- `create`: read-only `source` und veraenderbares `work` in WSL erzeugen
- `status`: registrierten Zustand und Trace-Daten lesen
- `collect`: Patch, Kandidat und kryptografische Hashes exportieren
- `validate`: den exportierten Kandidaten an den vorhandenen Validator reichen
- `destroy`: nur das eingegrenzte WSL-Session-Verzeichnis entfernen

Der Tool-Contract akzeptiert die von der API gesetzten Trace-Felder
`request_id`, `run_id`, `trace_session_id`, `source` und `context`. Dadurch
bleibt der Lifecycle auch ueber `POST /tools/wsl_session/invoke` nutzbar und
mit SYS-/Governance-Ereignissen korrelierbar.

Die eigentliche Ausfuehrung bleibt beim Tool `sys`. Dazu erhaelt `sys` die
optionale `workspace_session_id`; die Runtime setzt daraus Arbeitsverzeichnis,
Ausfuehrungsnutzer und Snapshot-Hash. Ein abweichendes `workdir` ausserhalb der
Session wird abgelehnt.

Damit bleiben Lifecycle, Ausfuehrung und Bewertung getrennt:

```text
wsl_session -> Workspace-Lifecycle
sys         -> policy-gated direkte Kommandos
ai-validator-> unabhaengige Kandidatenpruefung
Governance  -> Entscheidung ueber Uebernahme
```

Der reale Governance-Canary bestaetigt aktuell:

- eine nicht autorisierte Workspace-Mutation wird zentral blockiert;
- die Datei entsteht vor Approval nicht;
- der manuelle Proposal-/Decision-Pfad fuehrt exakt den gebundenen Write aus;
- Read-after-write und Mutation-Verification bestaetigen den Inhalt;
- Replay wird blockiert;
- die isolierte Session kann vollstaendig zerstoert werden.

Der Workspace-Agent uebergibt ein zentrales `governance_required`-Ergebnis
jetzt automatisch als Pending-Proposal. Dabei werden Kommando und Parameter
ueber denselben SHA-256-Digest wie am API-Gate gebunden. Der betroffene
`WorkspaceStepResult` und der gesamte Run wechseln auf `awaiting_decision`;
nachfolgende Schritte werden nicht freigegeben. Wiederholungen desselben
Run-/Step-/Digest-Tupels verwenden das bereits wartende Proposal und erzeugen
keine Duplikate. Die laufende API synchronisiert den gemeinsamen Store, sodass
das automatisch erzeugte Proposal unmittelbar ueber Liste, Decision und Audit
sichtbar ist.

Noch offen ist die automatische Fortsetzung des verbleibenden Workspace-Plans
nach einer Approval. Die genehmigte Einzelaktion kann bereits ueber den
gebundenen SYS-Invoke ausgefuehrt werden; eine persistierte Resume-Marke fuer
alle Folgeschritte ist noch nicht implementiert.

## Aktueller Befund

Die Tool-Flaeche ist klein, aber sicherheitskritisch. Erweiterungen sollten ueber `Tool`-Klassen, Registry und Tests erfolgen, nicht durch direkte Sonderpfade im Orchestrator.

Der Workspace-Agent darf fehlende Python-Pakete nicht per freiem `pip`
installieren. Der Runtime-Gate kann ausschliesslich den typisierten
`venv-pip install/show`-Pfad fuer Pakete aus
`LIARA_AGENT_DEPENDENCY_ALLOWLIST` einfuegen. Ziel ist immer die
Workspace-`.venv`; Installation und Verifikation sind getrennte SYS-Audits.

Der anschliessende Testpfad ist ebenfalls typisiert: Er erlaubt ausschliesslich
`python -m pytest` mit wenigen harmlosen Flags und einem expliziten relativen
Selektor unter `tests/`. Direkte Python-Skripte, absolute Pfade, `..`, fremde
Pytest-Konfigurationen und dynamische Plugins bleiben an der SYS-Policy
gesperrt. Der Workspace-Agent normalisiert generierte Testschritte auf
`python -m pytest -q tests` beziehungsweise einen engeren `tests/...`-Selektor.
