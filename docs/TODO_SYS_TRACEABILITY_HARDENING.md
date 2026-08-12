# TODO sys Traceability Hardening

Stand: 2026-07-13
Quelle: Cleanup-Pass fuer `sys`-Oberflaeche, `sys_audit`, CLI/TUI und aktuelle Audit-/Traceability-Befunde

## Ziel

Die verbleibenden `sys`-Callsites sollen Traceability konsequent und explizit durchreichen, damit `sys_audit` nicht mehr auf Fallbacks angewiesen ist.

Fokusfelder:

- `request_id`
- `run_id`
- `session_id`
- `source`
- `context`

Das Ziel ist nicht nur "kein Crash", sondern spaetere Schadensaufnahme, Forensik und Audit-Auswertung ohne Blindflug.

## Ausgangslage

Aktueller Stand:

- `sys_audit` ist fail-soft und import-sicher.
- `sys_audit` setzt Fallbacks fuer `request_id` und `source`.
- Die TUI zeigt `missing_request_id`, `missing_source` und `traceability_complete` bereits an.
- Ein Teil der Aufrufpfade reicht Traceability schon sauber durch, aber nicht alle.

Problem:

- Hohe `missing_request_id`-Werte sind ein Qualitaetsproblem.
- `traceability_complete = false` darf kein Normalzustand fuer aktive `sys`-Pfade bleiben.
- Fallbacks in `sys_audit` sind Sicherheitsnetz, nicht Soll-Zustand.

## Inventar-Snapshot (aktiv, 2026-07-13)

| callsite_id | file | symbol_or_endpoint | request_id | run_id | session_id | source | context | status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| cli_sys_direct | services/cli/main.py | cmd_sys | explicit | explicit | explicit | explicit | explicit | explicit |
| api_tools_invoke_boundary | services/api/app.py | POST /tools/{tool_name}/invoke | explicit (default if missing) | explicit (default if missing) | passthrough | explicit (default if missing) | explicit (default if missing) | explicit |
| orchestrator_sys_params | services/orchestrator/executor.py | _build_sys_parameters | explicit | explicit | explicit | explicit | explicit | explicit |
| orchestrator_julia_fallback_sys | services/orchestrator/executor.py | _execute_julia_compute (python fallback) | explicit | explicit | explicit | explicit | explicit | explicit |
| orchestrator_judge_pre_action | services/orchestrator/orchestrator.py | log_judge_pre_action callsites | fallback_only when run_id empty (falls back to session_id) | fallback_only when run_id empty | explicit | explicit | explicit | fallback_only |
| api_chat_safety_judge | services/api/app.py | chat_safety_pre/chat_safety_post | explicit | explicit | explicit | explicit | explicit | explicit |
| ops_live_simulation_invoke | scripts/live_simulation_mode_invoke_check.py | POST /tools/sys/invoke payloads | explicit | explicit | explicit | explicit | explicit | explicit |

Interpretation:

- `explicit`: Feld wird am Callsite aktiv gesetzt.
- `fallback_only`: Feld ist nur ueber Fallback-Strategie garantiert (z. B. leeres `run_id` -> `request_id=session_id`).
- `missing`: Feld bleibt leer/unklassifiziert (derzeit kein aktiver Fall im Inventar).

## Prioritaet P0

- [x] Alle aktiven `sys`-Callsites inventarisieren
  - Ziel: Vollstaendige Liste der aktiven Einstiegspfade nach `sys`
  - Kandidaten: API, Orchestrator, CLI, Judge-Pre-Action, Service-/Ops-Skripte
  - Akzeptanzkriterium: dokumentierte Liste der aktiven Callsites mit Datei + Funktion

- [x] Callsites ohne explizite `request_id` markieren
  - Ziel: alle Stellen identifizieren, die nur ueber Fallback `run_id/session_id` auditiert werden
  - Akzeptanzkriterium: pro Callsite klarer Status `explicit`, `fallback_only` oder `missing`

- [x] Callsites ohne explizite `source` oder `context` markieren
  - Ziel: Operatorisch brauchbare Herkunft und Intent sicherstellen
  - Akzeptanzkriterium: keine aktive `sys`-Callsite bleibt unklassifiziert

## Prioritaet P1

- [x] CLI-initiierte `sys`-Aufrufe mit stabiler Request-Identitaet versehen
  - Fokus: `services/cli/main.py`, Shell/TUI-Adapter
  - Soll: CLI setzt nicht nur `source="cli"`, sondern nach Moeglichkeit auch reproduzierbare Request-Metadaten
  - Akzeptanzkriterium: CLI-`sys`-Runs erscheinen im Audit mit explizitem `source`, sinnvoller `context`-Markierung und nicht nur impliziten Fallbacks
  - Status 2026-07-13: `cmd_sys(...)` setzt stabile `request_id/run_id`; REPL setzt `source=cli`; TUI-Shell setzt `source=tui` mit `context=tui.shell.sys`
  - Tests: `tests/unit/test_cli.py::test_cmd_sys_invokes_sys_tool_with_command_payload`, `tests/unit/test_cli.py::test_repl_sys_command_executes_locally`

- [x] Orchestrator-initiierte `sys`-Aufrufe vollstaendig durchziehen
  - Fokus: Planner/Executor/Dispatch-Pfade
  - Soll: `request_id`, `run_id`, `session_id`, `source`, `context` werden aus dem Laufkontext uebergeben
  - Akzeptanzkriterium: keine aktive Orchestrator-`sys`-Ausfuehrung verlaesst sich nur auf Audit-Fallbacks
  - Status 2026-07-13: `_build_sys_parameters(...)` und Julia->Python-Fallback in `services/orchestrator/executor.py` reichen alle Kernfelder explizit durch
  - Tests: `tests/unit/test_orchestration_split.py`

- [x] Judge-Pre-Action-Events konsistent annotieren
  - Fokus: `log_judge_pre_action(...)`
  - Soll: klare Herkunft und Stage-Kennzeichnung fuer `allow`, `revise`, `block`
  - Akzeptanzkriterium: Judge-Events sind im Audit eindeutig von CLI-/API-/Orchestrator-Ausfuehrungen unterscheidbar
  - Status 2026-07-13: `_execute_tools(...)` und FACT_LOOKUP-Logic-Guard in `services/orchestrator/orchestrator.py` nutzen jetzt zentrale Traceability-Zuweisung (inkl. Session-Fallback ohne `run_id`)
  - Tests: `tests/unit/test_orchestrator_fact_lookup_audit.py` deckt explizite Felder und Fallback-Verhalten ab

- [x] Service-/Ops-Pfade pruefen
  - Fokus: Service-Guard, Status-Checks, operative Hilfsskripte, Admin-TUI-nahe Pfade
  - Akzeptanzkriterium: aktive operative `sys`-Pfade tragen mindestens `source` und `context`, idealerweise auch `request_id` oder einen aequivalenten stabilen Korrelationswert
  - Status 2026-07-13: aktiver Ops-Pfad `scripts/live_simulation_mode_invoke_check.py` sendet explizit `request_id/run_id/session_id/source/context`; kein weiterer aktiver Script-Callsite auf `/tools/sys/invoke` gefunden

## Prioritaet P2

- [x] Audit-TUI um Traceability-Drilldown ergaenzen
  - Idee: Top-Kontexte/Top-Sources mit fehlender Traceability direkt sichtbar machen
  - Akzeptanzkriterium: Operator kann fehlende Traceability clusterweise erkennen statt nur Gesamtzaehler zu sehen
  - Status 2026-07-13: `services/tui/sys_audit_tui.py` zeigt jetzt Cluster `missing_by_source` und `missing_by_context` (Snapshot + Textual Event-Stream)
  - Tests: `tests/unit/test_sys_audit_tui.py::TestTraceabilityDrilldown`

- [x] Traceability-Regressionstests erweitern
  - Fokus: Unit- und Slice-Tests fuer CLI, Orchestrator, Judge und `sys_audit`
  - Akzeptanzkriterium: neue aktive `sys`-Callsites koennen nicht unbemerkt wieder ohne Metadaten eingefuehrt werden
  - Status 2026-07-13: Judge/Orchestrator-Slice erweitert (`tests/unit/test_orchestrator_fact_lookup_audit.py`), API-Safety/Invoke abgesichert (`tests/unit/test_api_app.py`), TUI-Drilldown abgesichert (`tests/unit/test_sys_audit_tui.py`)

- [x] Doku fuer `sys`-Contract nachziehen
  - Fokus: `docs/09_reference/SYS_AUDIT.md`, API-/Service-Doku
  - Akzeptanzkriterium: fuer neue `sys`-Callsites ist explizit dokumentiert, welche Felder Pflicht- bzw. Soll-Metadaten sind
  - Status 2026-07-13: `docs/09_reference/SYS_AUDIT.md` enthaelt jetzt Soll-Contract-Tabelle und Source/Context-Konventionen fuer aktive Pfade

## Konkrete Pruefkandidaten

- `services/cli/main.py`
- `services/tui/liara_shell_modules/commands.py`
- `services/orchestrator/executor.py`
- `services/orchestrator/orchestrator.py`
- `services/api/app.py`
- `services/tools/builtin/wsl_executor.py`
- aktive Service-/Admin-Skripte mit `sys`-nahen Ausfuehrungen

## Messbare Zielwerte

- `missing_request_id` in typischen aktiven `sys`-Flows deutlich reduziert
- `source=unknown` fuer aktive `sys`-Flows gegen null
- `traceability_complete=false` nur noch fuer bewusst tolerierte Alt- oder Sonderfaelle
- neue `sys`-Slices durch Tests gegen Traceability-Regression abgesichert

## Definition of Done

- [x] aktive `sys`-Callsites sind inventarisiert
- [x] jede aktive `sys`-Callsite hat explizite Entscheidung fuer `request_id`, `run_id`, `session_id`, `source`, `context`
- [x] CLI-, Orchestrator- und Judge-Pfade sind mit gezielten Tests abgesichert
- [x] Audit-TUI macht verbleibende Traceability-Luecken sichtbar
- [x] `SYS_AUDIT.md` beschreibt den Soll-Contract fuer neue `sys`-Callsites klar genug fuer Folgearbeit
