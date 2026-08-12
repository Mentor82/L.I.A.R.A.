# sys_audit

## Zweck

`sys_audit` ist der strukturierte Audit-Log fuer den `/sys`-Pfad in LIARA.
Er dokumentiert:

- blockierte `/sys`-Anfragen
- ausgefuehrte `/sys`-Anfragen
- Judge-Pre-Action-Entscheidungen im selben Logstrom
- minimale Traceability-Metadaten fuer spaetere Analyse

Das Modul lebt in `services/tools/builtin/sys_audit.py`.

## Log-Datei

Standardpfad:

```text
logs/services/sys_audit.jsonl
```

Jeder Eintrag wird als einzelnes JSON-Objekt in einer JSONL-Datei geschrieben.

Wenn der Dateihandler beim Import nicht erstellt werden kann, faellt `sys_audit` fail-soft auf einen `StreamHandler` zurueck. Der Import des Moduls bleibt damit nutzbar, auch wenn kein Schreibzugriff auf das Logfile besteht.

## Was geloggt wird

### Bei blockierten Requests

API:

```python
log_blocked(command, args, reason, ...)
```

Typische Felder:

- `command`
- `args`
- `policy_decision = "blocked"`
- `policy_reason`
- `request_id`
- `session_id`
- `run_id`
- `source`
- `context`
- `risk_level`
- `command_family`
- `traceability_complete`

### Bei ausgefuehrten Requests

API:

```python
log_executed(command, args, exit_code, duration_ms, stdout_bytes, stderr_bytes, ...)
```

Zusaetzlich relevant:

- `exit_code`
- `duration_ms`
- `stdout_bytes`
- `stderr_bytes`
- `is_network`
- `is_write`
- `http_method`
- `target_host`
- `outcome_class`

### Bei Judge-Pre-Action-Entscheidungen

API:

```python
log_judge_pre_action(tool_name=..., decision=..., ...)
```

Diese Eintraege verwenden denselben Audit-Log und erscheinen als:

- `command = "judge:<tool_name>"`
- `context = "judge_pre_action:<decision>"` wenn kein expliziter Kontext gesetzt wurde
- optionale Zusatzfelder wie `judge_score`, `risk_flags`, `judge_constraints`

## Privacy- und Datenumfang

`sys_audit` speichert bewusst keine rohen stdout-/stderr-Inhalte.
Gespeichert werden nur Metadaten:

- `stdout_bytes`
- `stderr_bytes`
- `stdin_bytes`
- `stdin_sha256`

Das ist absichtlich knapp gehalten, damit Audit-Auswertung moeglich bleibt, ohne komplette Nutzdaten in den Audit-Log zu kippen.

## Traceability-Regeln

Die wichtigsten Felder fuer spaetere Schadensaufnahme sind:

- `request_id`
- `run_id`
- `session_id`
- `source`
- `context`

### Soll-Contract fuer neue `/sys`-Callsites

Fuer aktive produktive Pfade gilt der folgende Soll-Contract:

| Feld | Erwartung | Hinweis |
| --- | --- | --- |
| `request_id` | Pflicht (explizit) | stabiler Korrelationswert pro Request/Run |
| `run_id` | Pflicht (explizit) | darf bei CLI/Ops identisch zu `request_id` sein |
| `session_id` | Soll (explizit, falls vorhanden) | bei sessionlosen Ops-Pfaden optional |
| `source` | Pflicht (explizit) | kein `unknown` in aktiven Pfaden |
| `context` | Pflicht (explizit) | stage-/intent-spezifisch, nicht leer |

Wenn ein Callsite diese Felder nicht explizit setzt, ist das als Qualitaetsluecke zu behandeln, auch wenn `sys_audit` ueber Fallbacks noch einen verwertbaren Eintrag erzeugt.

### Source- und Context-Konventionen

Empfohlene Herkunfts-/Intent-Werte fuer aktive Pfade:

- CLI REPL: `source=cli`, `context=cli.repl.sys`
- TUI Shell: `source=tui`, `context=tui.shell.sys`
- API Tool Invoke: `source=api`, `context=api.tools.<tool>.invoke`
- API Safety Judge: `source=api`, `context=chat_safety_pre_block|chat_safety_post_block`
- Orchestrator Dispatch: `source=orchestrator`, `context=<selector_context>`
- Judge Pre-Action: `source=orchestrator`, `context=judge_pre_action*`
- Ops Scripts: `source=script.<name>`, `context=<run_intent>`

Damit bleiben Event-Cluster im Audit stabil filterbar (z. B. TUI vs. CLI vs. API vs. Orchestrator).

### Fallback fuer `request_id`

Wenn keine `request_id` uebergeben wird, verwendet das Modul folgenden Fallback:

1. `request_id`
2. `run_id`
3. `session_id`
4. sonst `missing_request_id`

### Fallback fuer `source`

Wenn keine `source` uebergeben wird, wird `orchestrator` bevorzugt. Falls nichts Sinnvolles vorliegt, bleibt als Default `unknown`.

### Traceability-Marker im Eintrag

Jeder Eintrag enthaelt zusaetzlich:

- `traceability_complete`
- `traceability_missing_fields`

Damit kann spaeter direkt ausgewertet werden, welche Events fuer Forensik oder Incident Review zu duenn annotiert waren.

## Abgeleitete Felder

`SysAuditEntry.to_dict()` erweitert jeden Datensatz um normalisierte Felder wie:

- `command_family`
- `outcome_class`
- `risk_level`
- `is_network`
- `is_write`
- `http_method`
- `target_host`
- `has_stdin`
- `arg_fingerprint`

Diese Felder sind fuer TUI, Filterung und spaetere Analyse gedacht.

## Analyse-Helfer im Modul

Neben den Schreibfunktionen stellt `sys_audit` auch Lese- und Analysehelfer bereit:

```python
load_entries(path=None, limit=None)
filter_entries(entries, blocked_only=False, source=None, risk_level=None, command_family=None)
find_suspicious_entries(entries, limit=20)
summarize_entries(entries)
```

Kurz gesagt:

- `load_entries(...)` liest JSONL-Eintraege
- `filter_entries(...)` filtert nach operativen Dimensionen
- `find_suspicious_entries(...)` hebt problematische Events hervor
- `summarize_entries(...)` liefert kompakte Kennzahlen

## TUI-Nutzung

Die zugehoerige Ansicht liegt in `services/tui/sys_audit_tui.py`.

### Snapshot-Modus

```bash
python -m services.tui.sys_audit_tui --scope sys --limit 20
```

### Live-Refresh im Snapshot-Modus

```bash
python -m services.tui.sys_audit_tui --scope sys --follow
```

### Interaktive Textual-Oberflaeche

```bash
python -m services.tui.sys_audit_tui --scope sys --textual
```

Wichtige Filter:

- `--blocked-only`
- `--source orchestrator`
- `--risk-level high`
- `--command-family network`
- `--domain services`

## Beispiel eines Audit-Eintrags

```json
{
  "command": "curl",
  "args": ["-I", "https://example.com"],
  "policy_decision": "allowed",
  "policy_reason": null,
  "exit_code": 0,
  "duration_ms": 112.4,
  "stdout_bytes": 421,
  "stderr_bytes": 0,
  "request_id": "run-123",
  "session_id": "session-123",
  "run_id": "run-123",
  "source": "api",
  "context": "sys.invoke",
  "proposal_id": "sys-prop-example",
  "command_family": "network",
  "outcome_class": "success",
  "risk_level": "medium",
  "is_network": true,
  "is_write": false,
  "http_method": "GET",
  "target_host": "example.com",
  "traceability_complete": true,
  "traceability_missing_fields": [],
  "timestamp": 1710000000.0
}
```

## Operative Hinweise

- Fuer neue `/sys`-Callsites sollten `request_id`, `run_id`, `session_id`, `source` und `context` immer explizit mitgegeben werden.
- `traceability_complete = false` ist ein Qualitaetssignal und sollte nicht als normaler Endzustand akzeptiert werden.
- Judge-Eintraege liegen absichtlich im selben Logstrom, damit Block-, Revise- und Execute-Pfade zusammen analysiert werden koennen.
- Wenn die TUI leer wirkt, zuerst pruefen, ob `logs/services/sys_audit.jsonl` Eintraege enthaelt oder der Prozess auf den fail-soft Stream-Fallback gelaufen ist.
- Der Snapshot/TUI-Drilldown zeigt Cluster fuer fehlende Traceability (`missing_by_source`, `missing_by_context`) und hilft beim schnellen Finden systematischer Luecken.
- API- und UI-Abfragen mit `limit` lesen die JSONL-Datei blockweise vom Ende
  und JSON-parsen nur die benoetigten neuesten gueltigen Eintraege. Das Limit
  wird damit vor statt nach dem Parsing angewendet. Vollstaendige Auswertungen
  ohne Limit behalten bewusst den linearen Komplettscan.
- Der reale 22-MB-Snapshot mit 24.583 Zeilen benoetigte nach dieser Umstellung
  rund 8,8 ms fuer 200, 14,7 ms fuer 500 und 180,7 ms fuer 5.000 Eintraege.
  Dadurch blockiert der Architecture-Refresh die Async-API nicht mehr fuer
  mehrere Sekunden.
- `GET /admin/sys-audit/summary` trennt deshalb den Gesamtbestand als
  `summary.available_entries` vom tatsaechlich geladenen Fenster als
  `summary.inspected_entries`. `summary.total`, Block-, Risiko- und
  Mutationswerte beziehen sich weiterhin auf das geladene und gegebenenfalls
  gefilterte Fenster. Die Architecture Map kennzeichnet diese Reichweite
  explizit.

## Beziehung zum SYS-Governance-Audit

SYS Audit und Governance-Eventstream haben unterschiedliche Aufgaben:

- `logs/services/sys_audit.jsonl` belegt die reale Policy-Pruefung und
  Ausfuehrung des Tools.
- `logs/services/sys_governance_events.jsonl` belegt Proposal, menschliche
  oder Policy-Decision, Invocation-Attempt und Governance-Result.
- `proposal_id`, `request_id` und `run_id` verbinden beide Perspektiven. Bei
  governance-gebundenen Aufrufen wird `proposal_id` als eigenes SYS-Audit-Feld
  gefuehrt.

Der Governance-Eventstream ersetzt den SYS Audit nicht. Eine Approval ohne
SYS-Audit-Eintrag belegt keine reale Ausfuehrung; ein SYS-Audit-Eintrag ohne
erforderliche Approval ist bei aktivem `LIARA_SYS_GOVERNANCE_ENFORCE` nicht
zulaessig. Read-only Zugriff auf die Governance-Sequenz erfolgt ueber
`GET /tools/sys/governance/events?proposal_id=...`.

Der aktuelle zentrale Modus wird bevorzugt mit
`LIARA_SYS_GOVERNANCE_MODE=off|risk_based|all` gesetzt. Der historische
Boolean-Schalter bleibt als Alias fuer `all` erhalten. Im Modus `risk_based`
entsteht bei intern blockierten sensitiven SYS-Aufrufen ein strukturiertes
`governance_required`-Ergebnis; auch diese nicht ausgefuehrte Entscheidung ist
damit fuer Orchestrator und Operator sichtbar.

Policy-validierte read-only `curl`-Aufrufe bilden eine bewusste Ausnahme von
der pauschalen Netzwerkklassifizierung: W/G/B-Argumentpruefung und SYS Audit
bleiben aktiv, es entsteht jedoch kein Proposal. Ein Blacklist-Treffer bleibt
ein Policy-Denial und darf nicht in eine approvable Governance-Anfrage
umgedeutet werden.
