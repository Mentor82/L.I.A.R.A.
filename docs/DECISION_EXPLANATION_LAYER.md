# LIARA - Decision Explanation Layer

Status: implemented
Purpose: Deterministische, auditierbare Begruendung von Runtime-Entscheidungen

## 1. Ziel

Dieses Modul ergaenzt den Hybrid-Control-Mechanismus um eine explizite, strukturierte Entscheidungsbegruendung.

LIARA soll nicht nur entscheiden, sondern nachvollziehbar erklaeren:

- warum ein Control-Mode gewaehlt wurde
- welche Metriken ausschlaggebend waren
- wie sicher diese Entscheidung ist

## 2. Grundprinzip

Jede Entscheidung erzeugt zusaetzlich zur Aktion eine Erklaerung.

- Decision = was passiert
- Explanation = warum es passiert

## 3. Output-Struktur

`validation_result.decision_explanation` enthaelt:

- `primary_reason`: genau ein Hauptgrund
- `secondary_reasons`: optionale weitere Gruende
- `supporting_metrics`: nur entscheidungsrelevante Kennzahlen, maximal 5
- `thresholds`: soft/hard Grenzwerte
- `decision_confidence`: Heuristikwert in `[0.0, 1.0]`
- `decision_trace`: deterministische Pruefschrittfolge
- `decision_path`: Alias auf `decision_trace` fuer stabilere Client-Vertraege

Zusatz fuer kompakte Client-Auswertung:

- `validation_result.explainability.triggered_laws`
- `validation_result.explainability.decision_path`
- `validation_result.explainability.decision_confidence`
- `validation_result.explainability.risk_score`
- `validation_result.explainability.resolution_basis`
- `validation_result.threshold_adaptation` (Runtime-Adaptionsstatus fuer Session-Schwellwerte)

Zusaetzliche Konfliktaufloesung aus den Math-Signalen:

- `validation_result.math_signals.conflict_resolution.had_conflict`
- `validation_result.math_signals.conflict_resolution.winning_law`
- `validation_result.math_signals.conflict_resolution.winning_priority`
- `validation_result.math_signals.conflict_resolution.winning_weight`
- `validation_result.math_signals.conflict_resolution.overridden_laws`
- `validation_result.math_signals.conflict_resolution.strategy`

`validation_result.threshold_adaptation` enthaelt typischerweise:

- `applied`: ob eine Empfehlung in dieser Runde uebernommen wurde
- `reason`: bei Nicht-Anwendung (z. B. `disabled`, `insufficient_samples`)
- `previous`: vorherige soft/hard Schwellwerte
- `recommended`: vom Runtime-Audit empfohlene Schwellwerte
- `applied_profile`: effektiv uebernommene (geclampte) Schwellwerte
- `strategy`: aktuell `clamped_session_adaptation`

Rollback-Fall (Outcome-Guard aktiv):

- `rolled_back`: `true`, wenn adaptierte Schwellwerte zurueckgesetzt wurden
- `reason`: typischerweise `outcome_degraded`
- `previous_outcome` / `current_outcome`: Vergleichsbasis fuer die Rollback-Entscheidung
- `rollback_profile`: wiederhergestellte Basis-Schwellwerte
- `strategy`: `outcome_guarded_rollback`

Das gleiche Objekt wird ebenfalls in folgenden Audit-Pfaden abgelegt:

- `execution_trace` bei Transition `validation` unter `metadata.decision_explanation`
- `execution_trace` bei Transition `complete` unter `metadata.decision_explanation`
- `run_debug.decision_explanation`

## 4. Primary Reason

Erlaubte Werte:

- `policy_violation`
- `actionable_risk_exceeded_soft_limit`
- `actionable_risk_exceeded_hard_limit`
- `utility_negative`
- `score_fach_critical`
- `manual_override`
- `normal_operation`

Aktueller Runtime-Stand:

- `manual_override` ist fuer spaetere Policy-Overwrites reserviert.

## 5. Secondary Reasons

Aktiv verwendete Sekundaergruende:

- `rds_high`
- `cost_high`
- `score_code_weak`
- `score_robustheit_weak`
- `repeated_weak_scores`
- `context_entropy_high`

## 6. Ableitungsregeln

Regel E1 (Soft): wenn `actionable_risk > soft_max` dann `actionable_risk_exceeded_soft_limit`.

Regel E2 (Hard): wenn `actionable_risk > hard_max` dann `actionable_risk_exceeded_hard_limit`.

Regel E3 (Utility): wenn `utility < 0` dann `utility_negative`.

Regel E4 (Score): wenn `score_fach >= 5` dann `score_fach_critical`.

## 7. Prioritaet bei Mehrfachtrigger

Deterministische Reihenfolge:

1. `policy_violation`
2. `actionable_risk_exceeded_hard_limit`
3. `actionable_risk_exceeded_soft_limit`
4. `utility_negative`
5. `score_fach_critical`
6. `normal_operation`

## 8. Confidence-Heuristik

Wertbereich: `0.0 - 1.0`

- hoch: klare Regel-/Schwellwertverletzung
- mittel: mehrere begruendende Signale
- niedrig: schwache heuristische Evidenz

Die aktuelle Implementierung berechnet den Basiswert aus dem `primary_reason` und verstaerkt ihn leicht bei mehreren `secondary_reasons`.
