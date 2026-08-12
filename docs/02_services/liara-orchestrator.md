# Service: liara-orchestrator

Stand: 2026-08-11  
Code: `services/orchestrator/`

## Aufgabe

Der Orchestrator ist der Steuerkern fuer eine Anfrage. Er baut Kontext, trifft Routing- und Toolentscheidungen, ruft Tools und LLM-Provider auf, validiert Ergebnisse und persistiert relevante Informationen.

## Hauptfluss

```text
OrchestratorRequest
-> InputSituationProfiler
   -> RetrievalIntentAnalyzer
-> QueryRouter / LibrarianRouter
-> QueryPlanner
-> ContextController / EvidenceEngine
-> ToolExecutor
-> InferenceGateway oder InferenceInvoker
-> ResponseValidator / JudgeEngine / RewardModelScorer
-> Memory Adapter / Graph-v2 Persistenz
-> OrchestratorResponse
```

### Tool-Evidenz und Ausfuehrungswahrheit

Routingabsicht und erfolgreiche Ausfuehrung sind getrennte Zustaende.
`selected_tools` allein darf weder im Prompt noch im Validator als Beleg
gelten. Der Executor bereitet deshalb zuerst das konkrete
`ToolExecutionRequest` vor; der Pre-Action-Judge bewertet exakt dessen
`command`, `args`, Scope und Trace. Erst nach `allow` wird dasselbe Payload
ausgefuehrt. `revise` und `block` halten die Ausfuehrung zurueck.

Fehlgeschlagene Aufrufe werden nicht mehr verworfen. Sie erscheinen als
`kind=tool_execution_failure`, `status=failed`, `evidence=false`. Der Planner
kann den Fehler dadurch ehrlich benennen, waehrend Validator und
Confidence-Scoring ihn ausdruecklich nicht als Faktenevidenz zaehlen. Der
`tool_evidence_integrity`-Check blockiert behauptete Befehls-, API- oder
Toolresultate ohne erfolgreiche Ausgabe. Nach ausgeschoepfter Retry-Grenze
liefert der Orchestrator eine deterministische Meldung, dass kein belastbares
Tool-Ergebnis vorliegt.

### Eingangssituation: Analyze -> Think -> Answer / Plan / Act

Vor der Tool-Auswahl erzeugt der Orchestrator fuer jeden Turn ein typisiertes
`InputSituationProfile` (`liara.input-situation.v1`). Es verbindet die
eingehende Nachricht mit beobachtbaren Laufzeitsignalen: vorhandener
Session-History, Workspace-Verfuegbarkeit, Simulationsmodus, Request-Quelle und
Tokenlimit. Das Profil enthaelt Verarbeitungsebene und -kette, Themen- und
Fachprofil, Kontextabhaengigkeit, Externalitaet, Komplexitaet, Ambiguitaet,
Risiko, Eingangsmood und ein vor Ausfuehrung begrenztes Ressourcenbudget.

Die deterministische Baseline ist immer verfuegbar. Mit
`INPUT_PROFILER_USE_EMBEDDINGS=true` kann sie durch reale Scout-Embeddings
angereichert werden. Das Profil ist kein Berechtigungsnachweis: Router-Policy,
Tool-Allowlist, Judge und Validator bleiben nachgelagert und verbindlich. Mood
darf nur Ton und Darstellungsform beeinflussen, niemals Wahrheit, Rechte oder
Validierungsgrenzen.

Das Profil erscheint in der Tool-Selection-Transition und unter
`llm_generation.context_debug.input_profile`. Das Fibonacci-artige Budget
begrenzt die moeglichen Refinement-Schleifen und wird im Reasoning-Metrics-
Snapshot gegen die beobachtete Ausfuehrung ausgewiesen.

### Inferenzgesteuerte Web-Recherche

Externer Informationsbedarf wird nicht ueber eine feste Domaenen- oder
Quellen-Schlagwortliste erkannt. Der `RetrievalIntentAnalyzer` zerlegt die
Anfrage vor dem Routing semantisch in Ziel, Entitaeten, vermutete Quelle,
Unsicherheiten, Suchanfrage und optionale Kandidaten-URL. Dieser Intent ist
Routinginput, aber weder Berechtigung noch Evidenz.

Bei einer sicheren konkreten URL folgt ein read-only SYS-Abruf. Bei
Unsicherheit fragt LIARA zunaechst eine begrenzte Suchseite ab (aktuell Bing
RSS), bewertet die Kandidaten in einer zweiten Inferenz und fuehrt hoechstens
einen Primaerabruf aus. Suchtreffer tragen `evidence_scope=discovery` und
duerfen die Antwort nicht erden. Die ausgewaehlte URL durchlaeuft deshalb
erneut URL-Validierung, W/G/B, den exakten Pre-Action-Judge, optionale
Governance und SYS-Audit. Details: ADR-005.

## Wichtige Module

| Modul | Rolle |
| --- | --- |
| `orchestrator.py` | Fassade & Coordinator (Pipeline-Reihenfolge, State & 100% monkeypatch-kompatible Delegierung) |
| `reasoning_control.py` | Phase 1–4 Reasoning-Metriken (Belief, Utility, Stability, Decision), Julia/Python-Reasoning, Adaptionen |
| `librarian_pipeline.py` | Laden aller expliziten Kanäle (History, Facts mit `[fact_verified:ns]`, Reranked Vector Retrieval & Graph-Relational-Context) |
| `tool_discovery.py` | Tool-Selektion, Tool-Ausführung, External Tool Planning & Web-Discovery-Ranking |
| `generation_pipeline.py` | LLM-Inferenz, NPU-Offload, Prompt-Bau, Response-Validierung, Judge Traceability & Audit-Log-Integration |
| `input_profiler.py` | Eingangssituation, Mood, Fachprofil und Ressourcenbudget |
| `retrieval_intent.py` | semantische Erkennung und Zerlegung externer Informationsbeduerfnisse |
| `router.py` | Query-Routing |
| `planner.py` | Planung, Sprache/Intent, Tool- und Step-Vorbereitung |
| `executor.py` | Toolausfuehrung |
| `validator.py` | Response-Validierung |
| `context_controller.py` | Kontextsteuerung |
| `evidence_engine.py` | Evidenzaufbau |
| `gap_detector.py` | Erkennung fehlender Antwortbestandteile |
| `graph_v2_persistence.py` | Persistenz von Runs in Graph-v2 |
| `reasoning_math*.py` | Reasoning-Metriken und Schwellen |
| `defs/*` | Ausgelagerte Hilfslogik fuer Prompting, Routing, Context, Judge, Artifacts |
| `workspace_agent.py` | typisierte mehrstufige Workspace-Auftraege, WSL-Verifikation und Validator-Gate |

Workspace-Folgefragen wie `analysiere den Fehler`, `was ist fehlgeschlagen?`
oder Fragen nach Validator-Findings laden das neueste sessiongebundene
`workspace_agent_run`-Artefakt. Bei fehlgeschlagenen RUN-Schritten enthaelt es
einen begrenzten, redigierten Diagnoseauszug. Die finale Antwort wird aus
diesem Artefakt geerdet; ohne Artefakt darf kein Ausfuehrungsergebnis erfunden
werden.

Reparatur- und Aktualisierungsauftraege erhalten vor der eigentlichen Planung
einen read-only, policy-gated Workspace-Preflight. `find` inventarisiert den
realen WSL-Root bis Tiefe 4; `.venv`, Git-, Cache- und LIARA-Artefaktpfade
werden entfernt. Das auf 128 Pfade beziehungsweise 12000 Zeichen begrenzte
relative Inventar wird dem Planner als autoritativer Ist-Zustand uebergeben.
Er darf insbesondere kein konventionelles `src/` voraussetzen, wenn dieses im
Inventar nicht existiert. Der Preflight erscheint mit
`context=agent_workspace_preflight_inventory` im SYS-Audit.

Bei Reparaturen folgt auf das Inventar ein ebenfalls policy-gated
Quell-Snapshot. Relevante `.py`-Dateien und `pyproject.toml` werden ueber
einzelne `cat`-Schritte gelesen; Tests erhalten Prioritaet, damit bestehende
Import- und Ausgabe-Contracts vor der Mutation sichtbar sind. Die Obergrenzen
werden aus `hard_max / gamma_tools` (Dateien) und
`hard_max / beta_tokens` (Textmenge) abgeleitet und zusaetzlich auf 8 Dateien
sowie 32000 Zeichen gedeckelt. Versteckte Dateien und Runtime-Verzeichnisse
bleiben ausgeschlossen. Audit-Kontext: `agent_workspace_preflight_read`.

## Aktive Steuerungsmerkmale

- Routing ueber Heuristiken, semantische Signale und Reward-Routing.
- Provider-Auswahl mit Defaults aus `Settings`.
- NPU-Helper-Offload fuer kleine Extraktions-/Co-Worker-Aufgaben.
- Judge-Profile fuer riskante Aktionen wie `sys`, `compute.run`, `compute.generate`.
- Bindung des Pre-Action-Judge an das konkret vorbereitete Ausfuehrungspayload.
- Explizite nicht-erdende Failure-Envelopes und Tool-Evidence-Integrity-Gate.
- Inferenzgesteuerte Web-Discovery ohne Quellen-Schlagwortliste; Suchsnippets
  bleiben nicht-erdende Kandidaten und jede Ziel-URL wird separat geprueft.
- Latency-Scope-Logging nach `logs/services/orchestrator/latency_scope.jsonl`, wenn aktiviert.
- Graph-v2-Persistenz nach erfolgreichem Lauf.
- Komplexe Coding-/Workspace-Auftraege werden ueber einen begrenzten
  Plan/Act/Observe-Pfad abgewickelt; jeder Schritt bleibt ein vorhandener
  `sys`-Toolcall.
- Provider-/Helper-Auswahl liegt in `defs/provider_selection.py`. Sie ist kein
  globaler Ressourcen-Scheduler und nutzt LiNeP nicht direkt.

## Konfiguration

Zentrale ENV-Werte aus `services/config/settings.py`:

- `DEFAULT_LLM_PROVIDER`
- `MAX_REASONING_STEPS`
- `MAX_STEP_CONTEXT_TOKENS`
- `EVIDENCE_REASONING_STEPS`
- `SEMANTIC_ROUTING_ENABLED`
- `REWARD_ROUTING_ENABLED`
- `REWARD_JUDGE_ENABLED`
- `NPU_HELPER_OFFLOAD_ENABLED`
- `RETRIEVAL_INTENT_PROVIDER`
- `RETRIEVAL_CANDIDATE_PROVIDER`
- `CO_WORKER_PROVIDER_LOCK_ENABLED`
- `LATENCY_SCOPE_ENABLED`

## Aktueller Befund

Der Orchestrator ist der fachlich dichteste Teil des Systems. Viele
Teilfunktionen wurden bereits in `defs/` ausgelagert; `orchestrator.py` bleibt
aber ein grosser Integrationspunkt. Fuer Tool-Evidenz und inferenzgesteuerte
Web-Recherche bestanden am 2026-08-11 338 fokussierte Retrieval-, API-,
Policy-, Judge-, Validator- und Orchestrator-Tests. Der vollstaendige
Unit-Lauf bleibt offen; zwei bestehende Tests rufen
`_build_embedding_query` weiterhin statisch statt als Instanzmethode auf.
Details und Akzeptanzkriterien: `docs/00_index.md`, ADR-004 und ADR-005.
