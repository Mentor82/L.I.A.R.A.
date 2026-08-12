# LIARA Judge v1

Status: implemented core pipeline, current as of 2026-04-19

## Ziel

Der Judge ist die zentrale Regelwerkschicht fuer LIARA. Er sorgt dafuer, dass
Agent-Aktionen, Tool-Aufrufe und Antworten innerhalb definierter Grenzen bleiben.

Der Judge entscheidet nicht ueber das fachliche Ziel, sondern ueber:

- Erlauben oder Blockieren einer Aktion
- Begrenzen von Risiko, Scope und Ausfuehrung
- Qualitaets-/Sicherheitsfreigabe von Antworten

## Ist-Stand

Die Basis ist nicht mehr nur verteilt vorhanden, sondern als einheitlicher Runtime-Pfad verdrahtet:

- Output-Validation: `services/orchestrator/validator.py`
- Sys/Command-Policy: `services/tools/builtin/wsl_executor.py`
- Command-Profiling W/G/B: `services/tools/builtin/sys_command_policy.py`
- Persistente Policy-DB: `services/tools/builtin/policy_db.py`
- Audit + Risiko-Metriken: `services/tools/builtin/sys_audit.py`
- Judge-Contracts/Engine: `services/judge/contracts.py`, `services/judge/engine.py`
- Reward-Erweiterung: `services/judge/adapters/reward_model_pre_action_adapter.py`, `services/judge/adapters/reward_model_post_action_adapter.py`
- Regeln/Tests: `docs/VALIDATOR_REGELSET.md`, `tests/unit/test_validator.py`, `tests/unit/test_judge_engine.py`, `tests/unit/test_judge_engine_reward_integration.py`

Kernaussage: Judge v1 ist im aktuellen Code als Pipeline aktiv. Offen sind vor allem Ausbau, Profilverbreiterung und feinere Runtime-/Monitoring-Fragen.

## Judge v1 Prinzip

Freiheit innerhalb definierter Grenzen.

Jede relevante Aktion durchlaeuft denselben Judge-Lebenszyklus:

1. Pre-Action Judge
2. Runtime Guard
3. Post-Result Judge
4. Audit/Trace

## Einheitliche Gesetzesgrundlagen (tool-agnostisch)

Diese Grundlagen gelten fuer jedes Tool gleich, unabhaengig davon ob es
`sys`, `compute.run` (Julia) oder ein spaeteres neues Tool ist.

### Gesetz 1: Safety vor Funktion

- Kritische Sicherheitsverletzungen fuehren immer zu `block`.
- Kein Profil darf Safety-Blockaden aufheben.

### Gesetz 2: Policy zuerst, dann Ausfuehrung

- Jede Aktion braucht vor Ausfuehrung eine Pre-Action-Pruefung.
- Ohne gueltige Pre-Action-Entscheidung keine Ausfuehrung.

### Gesetz 3: Runtime ist begrenzt

- Jede Ausfuehrung hat definierte Limits (Timeout, Scope, Inputgroesse, Retries).
- Limit-Verletzungen werden als Judge-Ereignis mit Reason-Code geloggt.

### Gesetz 4: Ergebnis braucht Freigabe

- Kein Resultat wird ungeprueft ausgegeben.
- Jede Ausgabe durchlaeuft Post-Result-Pruefungen (Safety, Konsistenz, Grounding).

### Gesetz 5: Jede Entscheidung ist nachvollziehbar

- Jede `warn|revise|block` Entscheidung braucht maschinenlesbaren `reason_code`.
- Jede Entscheidung wird mit Stage, Actor, Request-ID auditiert.

## Tool-Klassen unter derselben Rechtsordnung

Judge v1 trennt Regeln in zwei Ebenen:

- Verfassungsebene (global): die 5 Gesetze oben, immer fuer alle Tools.
- Fachrechtsebene (tool-spezifisch): zusaetzliche Regeln pro Tool-Klasse.

Tool-Klassen (v1):

- System-Tools (`sys`): Shell/Datei/Netzwerk-Risiken
- Compute-Tools (`compute.run` / Julia): numerische Modelle, deterministische Berechnung
- Wissens-Tools (historisch z. B. `web_search`, aktuell meist ueber `sys`-/Lookup-Pfade abgebildet): Quellenbezug und Grounding
- Zukuenftige Tools: bekommen eigenes Fachrecht, aber dieselbe Verfassung

## Judge v1 Entscheidungsmodell

Einheitliche Entscheidungen:

- `allow`: Aktion/Antwort freigegeben
- `warn`: freigegeben mit Auflagen/Hinweis
- `revise`: nicht freigeben, neu generieren/reparieren
- `block`: hart blockieren

Prioritaetsreihenfolge bei Konflikten:

1. Safety/Security
2. Policy/Compliance
3. Tool-Wahrheit/Konsistenz
4. Grounding/Factual Quality
5. Stil/Format

## Einheitlicher Judge Contract

### JudgeContext

```json
{
  "request_id": "uuid",
  "stage": "pre_action|runtime|post_result",
  "actor": "orchestrator|tool|worker",
  "intent": "chat|sys|simulation|memory|admin",
  "action": "tool_name_or_operation",
  "input": {},
  "metadata": {
    "source": "chat|orchestrator|api",
    "risk_hint": "low|medium|high"
  }
}
```

### JudgeCheckResult

```json
{
  "check": "safety|policy|consistency|grounding|runtime_limits",
  "status": "pass|fail|skip",
  "severity": "low|medium|high|critical",
  "reason_code": "policy.blocked_flag",
  "message": "human readable summary"
}
```

### JudgeDecision

```json
{
  "decision": "allow|warn|revise|block",
  "passed": true,
  "confidence": 0.82,
  "checks": [],
  "issues": [],
  "constraints": {
    "max_tokens": 1200,
    "tool_allowlist": ["sys", "compute.run"]
  },
  "next_action": "continue|retry|abort"
}
```

## Judge Pipeline v1

### Phase A: Pre-Action Judge

Zweck:

- Tool-/Action-Zulaessigkeit pruefen
- Scope/Path/Host/Flag-Regeln anwenden
- ggf. constrain statt sofort block

Abbildung auf bestehenden Code:

- `sys` Tool: `services/tools/builtin/wsl_executor.py`
- Command-Policies: `services/tools/builtin/sys_command_policy.py`
- Policy-DB: `services/tools/builtin/policy_db.py`

### Phase B: Runtime Guard

Zweck:

- Timeouts, Input-Groessen, Write-Scope, Retry-Limits
- Laufzeitrisiko beobachten

Abbildung auf bestehenden Code:

- `wsl_executor` timeouts/workdir guards
- `simulation` (Julia) timeout + allowlist

### Phase C: Post-Result Judge

Zweck:

- Antwortqualitaet, Grounding, Konsistenz, Safety

Abbildung auf bestehenden Code:

- `services/orchestrator/validator.py`

### Phase D: Audit/Trace

Zweck:

- Entscheidungen nachvollziehbar machen
- Drift, Fehlregeln und Risiko-Hotspots sichtbar machen

Abbildung auf bestehenden Code:

- `services/tools/builtin/sys_audit.py`

## Profile (v1)

Vordefinierte Judge-Profile fuer unterschiedliche Betriebsmodi:

- `strict`
- `balanced`
- `exploratory`

Beispielverhalten:

- `strict`: warn -> revise, niedrige Schwellwerte fuer Block
- `balanced`: warn erlaubt, revise bei klaren Defekten
- `exploratory`: mehr warn statt block, aber harte Safety bleibt block

### Julia-Freiheitsprofil innerhalb des Judge

Julia soll frei nutzbar sein, aber kontrolliert und reproduzierbar:

- Frei in der Modelllogik: numerische Berechnungen im Modell sind nicht kuenstlich eingeschraenkt.
- Kontrolliert in der Ausfuehrung: allowlist-basierte Modellfreigabe, Timeouts, JSON I/O-Vertrag.
- Einheitlich bewertet: dieselben globalen Entscheidungen `allow|warn|revise|block`.

Konsequenz:

- Julia ist kein Sonderfall ausserhalb des Judge.
- Julia ist ein Compute-Tool mit hoher Freiheit innerhalb klarer Betriebsgrenzen.

## Implementierter Kernpfad

Der aktuelle Judge-Lebenszyklus laeuft heute ueber folgende Runtime-Punkte:

1. `Orchestrator._execute_tools()` baut `JudgeContext` fuer Pre-Action.
2. `JudgeEngine.evaluate_pre_action()` dispatcht auf:
   - `evaluate_pre_action_simulation_mode()`
   - `evaluate_pre_action_sys()`
   - `evaluate_pre_action_simulation()`
   - `evaluate_pre_action_compute_generate()`
3. Optional wird ein Reward-Model-Decision-Zweig zugemischt; strengere Entscheidungen gewinnen.
4. `JudgeEngine.evaluate_post_result()` validiert Antworten ueber den Validator und optional das Reward-Model.

## Offene Ausbaupfade

Diese Punkte bleiben weiterhin sinnvoll, sind aber keine Grundvoraussetzung mehr:

- weitere Tool-Klassen unter dieselbe Judge-Contract-Schicht ziehen
- Runtime-Guard-Ereignisse expliziter normalisieren
- Monitoring/Drift-Analyse fuer Reward- und Validator-Entscheidungen erweitern
- Wissens-/Web-Tools fachrechtlich sauber profilieren

Neu:

- `services/judge/engine.py`

API:

- `evaluate_pre_action(context) -> JudgeDecision`
- `evaluate_post_result(context) -> JudgeDecision`

### Schritt 4: Orchestrator-Anbindung

Anpassen:

- `services/orchestrator/orchestrator.py`

Fluss:

1. vor Tool-Dispatch: `evaluate_pre_action`
2. nach Antwort: `evaluate_post_result`
3. Entscheidung in Response-Metadaten aufnehmen

### Schritt 5: Unified Judge Audit

Neu:

- `services/judge/audit.py`

Inhalt:

- einheitliches Entscheidungsevent mit `reason_code`
- Referenz auf request_id, stage, actor, decision, confidence

## Acceptance Criteria fuer Judge v1

- Jede riskante Aktion hat eine explizite Judge-Entscheidung.
- Entscheidungen sind stage-basiert und maschinenlesbar.
- `allow|warn|revise|block` gilt systemweit konsistent.
- Jede Block-Entscheidung hat `reason_code` und Trace.
- Tests decken Pre-Action und Post-Result jeweils separat ab.
- Dasselbe Regelwerk gilt fuer `sys`, Julia-Compute und neue Tools.

## Teststrategie v1

Neue Testgruppen:

- `tests/unit/test_judge_contracts.py`
- `tests/unit/test_judge_engine.py`
- `tests/unit/test_judge_pre_action_adapter.py`
- `tests/unit/test_judge_post_result_adapter.py`
- `tests/unit/test_judge_pre_action_simulation_adapter.py`

Integration:

- `tests/integration/test_orchestrator_judge_flow.py`

Pflichtfaelle:

- policy-kritischer sys call -> `block`
- unsichere Antwort -> `block`
- schwach gegruendete Antwort -> `warn|revise`
- valide Tool-Antwort -> `allow`
- Julia-Modell in Allowlist + gueltiger Input -> `allow`
- Julia-Modell ausserhalb Allowlist -> `block`
- Neues Tool ohne Fachrecht-Profil -> `block` (default deny bis Profil vorhanden)

## Onboarding-Regeln fuer neue Tools

Jedes neue Tool muss vor Aktivierung folgende Mindestpunkte erfuellen:

1. Tool-Klasse definieren (System, Compute, Wissen, sonstige)
2. Fachrecht-Profil hinterlegen (Pre-Action + Runtime-Regeln)
3. Reason-Codes definieren (maschinenlesbar)
4. Judge-Adapter registrieren
5. Unit-Tests fuer allow/warn/revise/block liefern

Default-Policy:

- Kein Profil == kein Produktivstart (`block` by default)

## Migrationshinweise

- Kein Big-Bang erforderlich.
- Bestehende Module bleiben initial unveraendert und werden nur adaptiert.
- `docs/VALIDATOR_REGELSET.md` nach Einfuehrung auf Judge-v1 Contract referenzieren.

## Kurzfazit

LIARA hat die wichtigsten Judge-Bausteine bereits produktiv. Judge v1 ist
vor allem ein Architektur- und Vertrags-Refactor: vereinheitlichen, nicht
neu erfinden.
