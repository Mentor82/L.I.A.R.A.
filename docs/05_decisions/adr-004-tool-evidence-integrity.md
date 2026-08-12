# ADR-004: Tool-Evidenz ist an reale Ausfuehrung gebunden

Stand: 2026-08-11  
Status: angenommen und implementiert

## Kontext

Eine Web-Session zeigte einen systemischen Fehler: Der Router waehlt `sys`,
die konkrete Ausfuehrung scheitert oder wird von Governance/Judge nicht
freigegeben, das Modell erhaelt aber weiterhin `tools_used=["sys"]`. Weil
fehlgeschlagene Ergebnisse zuvor aus `tool_outputs` entfernt wurden, konnte
das Modell die Leerstelle mit erfundenen Befehlen, JSON-Antworten und
Quellenmarkern fuellen. Der Validator behandelte zugleich jedes nichtleere
Tool-Dictionary als Evidenz.

Der Defekt ist domaenenunabhaengig. Er betrifft potentiell Webabfragen,
Dateizugriffe, Healthchecks, Berechnungen und externe Tool-Bridges.

## Entscheidung

LIARA verwendet fuer Tool-Evidenz folgende verbindliche Invarianten:

1. Der Pre-Action-Judge bewertet das exakt vorbereitete
   `ToolExecutionRequest.parameters`-Payload, das anschliessend ausgefuehrt
   wird. Eine abstrakte Werkzeugbezeichnung ist keine Freigabe.
2. `revise` ist kein Ausfuehrungshinweis. Die Ausfuehrung wird bis zu einem
   gueltigen Payload zurueckgehalten.
3. Fehlgeschlagene, geblockte oder verweigerte Aufrufe bleiben als
   `tool_execution_failure` sichtbar und tragen immer `evidence=false`.
4. Nur erfolgreiche Toolausgaben duerfen Grounding, Attribution oder einen
   Confidence-Bonus erzeugen.
5. Behauptet eine Antwort ohne erfolgreiche Toolausgabe dennoch eine
   Ausfuehrung oder ein API-/Toolresultat, blockiert der
   `tool_evidence_integrity`-Check die Antwort.
   Der Post-Result-Judge erhaelt dafuer dieselben konkreten `tool_outputs` wie
   der ResponseValidator; `tools_used` allein ist weder positive noch negative
   Ausfuehrungsevidenz.
6. Bleibt nach den Retry-Grenzen keine Evidenz, ersetzt der Orchestrator den
   Entwurf durch eine deterministische, ehrliche Fehlermeldung.
7. Interne Marker wie `[SYS: ...]`, `[TOOL_RESULT: ...]` und Varianten von
   `[KNOWLEDGE_REFERENCE]` werden an der API-Ausgabegrenze entfernt.

## Vertrag

```text
ToolSelection
-> Prepared ToolExecutionRequest(command + args + trace)
-> Pre-Action Judge
   +-> allow -> exakt dieses Payload ausfuehren
   +-> revise/block -> nicht ausfuehren
-> ToolExecutionResult
   +-> success -> belastbare Evidenz -> Planner + Validator
   +-> failed  -> kind=tool_execution_failure, evidence=false
-> ResponseValidator(tool_evidence_integrity + grounding)
-> sichere Antwort oder belegtes Ergebnis
```

## Konsequenzen

- `selected_tools` beschreibt weiterhin die Routingabsicht, nicht den Erfolg.
- Fehlermetadaten bleiben fuer Diagnose, Trace und Benutzerkommunikation
  erhalten, duerfen aber keine Fakten erden.
- Ein Toolmarker ist niemals selbst ein Quellenbeleg.
- Fachspezifische Plausibilitaetsregeln koennen weiterhin ergaenzen, ersetzen
  aber nicht die allgemeine Provenienzbindung.

## Evidenz

- `tests/unit/test_orchestration_split.py`: identisches Judge-/Executor-Payload
  und persistierte Failure-Envelopes.
- `tests/unit/test_validator.py`: erfundene SYS-, API- und direkte Faktenclaims
  ohne erfolgreiche Evidenz werden abgewiesen.
- `tests/unit/test_output_sanitizer.py`: parametrisierte, Unicode- und
  fehlerhaft geschriebene interne Marker werden entfernt.
- Fokussierte Baseline am 2026-08-11: 168 API-/Executor-/Validator-Tests,
  52 Judge-/Coordinator-Tests und 28 weitere Orchestrator-Tests bestanden.
- Live-Canary `23566477-4f29-4c97-b32f-322a71b7feda`: Netzwerkzugriff durch
  die damalige pauschale Governance geblockt, `status=failed`,
  `evidence=false`, sichere Antwort, `validation_passed=true` und kein
  Artefakt projiziert. Die spaetere W/G/B-Korrektur ist in den SYS-
  Sicherheitsdokumenten beschrieben.
- Live-Canary `a771ab1d-f262-4c63-b397-000c1a862289`: W/G/B-validierter
  Scryfall-Abruf in Debian WSL erfolgreich; `sys=success`, keine Governance-
  Anforderung, Antwort `Clay Revenant / Lehm-Wiedergänger`, ResponseValidator
  `accept`. Der dabei sichtbare fehlende Tooloutput im Post-Result-Judge wurde
  als Contract-Luecke geschlossen.
