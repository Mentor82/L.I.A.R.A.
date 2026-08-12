# Liara Validator Regelset (v0.2)

## Ziel

Der Validator prueft Antworten vor der Ausgabe auf Qualitaet, Konsistenz, Grounding und Sicherheit.
Er entscheidet nicht ueber Intent oder Routing, sondern ueber Ausgabefaehigkeit.

## Scope und aktuelle Anbindung

Der aktive Validator laeuft aktuell in der Orchestrator-Pipeline:

- services/orchestrator/validator.py
- services/orchestrator/orchestrator.py
- services/contracts/service_boundaries.py (ValidationResult)

Der Validator liefert jetzt explizit:

- passed
- decision: accept | revise | warn | block
- checks: pass/fail/skip pro Check
- issues
- confidence_score
- suggestions

## Pruefstufen (aktuell implementiert)

1. source_attribution
- Wenn Tools genutzt wurden, muss die Antwort auf Wissen verweisen.
- Derzeit: Marker [KNOWLEDGE_REFERENCE] als Mindestsignal.

2. consistency
- Platzhalter fuer Tool/Antwort-Konsistenz.
- Aktuell als pass implementiert, fuer v0.3 zu vertiefen.

3. length
- Guardrail gegen unbrauchbar kurze oder ueberlange Antworten.

## Entscheidungsmodell

Die Decision wird aus Issues und confidence_score abgeleitet:

- block
  - sicherheits/policy-kritische Issues
  - oder confidence < 0.45
- revise
  - formale Defekte (z. B. zu kurz/zu lang/invalid)
  - oder restliche Issues bei hoherer Confidence
- warn
  - confidence zwischen 0.45 und 0.75
  - Ausgabe nur mit Unsicherheitsrahmen
- accept
  - keine Issues, ausreichende Confidence

## Strict vs Non-Strict

- strict_mode = true
  - nur accept gilt als passed
- strict_mode = false
  - accept und warn gelten als passed

Hinweis: Der Orchestrator nutzt aktuell strict_mode=false.

## Prioritaetsregeln

Bei Konflikten gilt:

1. Safety/Policy
2. Tool-Wahrheit
3. Strukturierte Fakten
4. Konsistenz
5. Retrieval-Aehnlichkeit
6. Stil

## Mindestregeln

Regel 1
- Keine Antwort ungeprueft ausgeben.

Regel 2
- Formale Korrektheit geht vor Stil.

Regel 3
- Fakten brauchen Grounding.

Regel 4
- Unsicherheit ist erlaubt, unbegruendete Sicherheit nicht.

Regel 5
- Tool-Output hat Vorrang vor Modellvermutung.

Regel 6
- Keine erfundenen Quellen/Tools/Belege.

## Ergebnisbeispiel (ist-nah)

```json
{
  "passed": true,
  "decision": "warn",
  "checks": {
    "source_attribution": "pass",
    "consistency": "pass",
    "length": "fail"
  },
  "issues": [
    "Response too short"
  ],
  "confidence_score": 0.5,
  "suggestions": [
    "Output can be shown with uncertainty note."
  ]
}
```

## Rollout-Backlog (naechste Schritte)

v0.3
- consistency check gegen tool_outputs wirklich implementieren
- grounding check mit explizitem Evidence-Matching
- safety check mit policy keyword + context classifier

v0.4
- per-check metrics und trendbare Telemetrie
- validator profile (strict, balanced, exploratory)
- auto-repair loop fuer revise

## Tests

Abgedeckt ueber:

- tests/unit/test_validator.py

Empfohlene weitere Tests:

- Tool widerspricht Antwort -> decision revise/block
- Retrieval leer + starke Faktbehauptung -> warn
- policy-kritische Anfrage -> block
