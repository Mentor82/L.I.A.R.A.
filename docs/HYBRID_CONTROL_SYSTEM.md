# LIARA - Hybrid Control Schema
Status: draft
Purpose: Copilot-safe, deterministic control model for advisory + soft-control + hard-control decisions.

---

## 1. Ziel

Dieses Schema definiert, wie LIARA mathematische Reasoning-Metriken und nachgelagerte Qualitätsbewertungen kombiniert, um Laufzeitentscheidungen kontrolliert zu beeinflussen.

Das Modell ist bewusst hybrid:

- Advisory: beobachten und markieren
- Soft-Control: Verhalten gezielt anpassen
- Hard-Control: nur bei klaren, kalibrierten Risikofällen erzwingen

Ziel ist nicht maximale Härte, sondern kontrollierbare, nachvollziehbare Systemführung.

---

## 2. Grundprinzip

LIARA bewertet zwei verschiedene Ebenen:

### A. Vor der finalen Ausgabe
Laufzeit- und Entscheidungsmetriken:

- `cost_total`
- `rds_v2`
- `risk_total`
- `actionable_risk`
- `utility`

Diese Werte steuern den Laufweg.

### B. Nach der finalen Ausgabe
Ergebnis- und Qualitätsmetriken:

- `score_fach`
- `score_code`
- `score_robustheit`
- `score_gesamt`

Diese Werte bewerten das Resultat.

---

## 3. Leitsatz

Vor der Antwort entscheidet das System:
- Wie weit darf ich gehen?
- Wie teuer wird weiteres Denken?
- Wie riskant ist weitere Exploration?

Nach der Antwort bewertet das System:
- War das Ergebnis fachlich korrekt?
- War der Code sauber?
- War das Verhalten robust?

---

## 4. Steuerstufen

### 4.1 Advisory

Advisory erzeugt nur Signale, keine harte Verhaltensänderung.

Eigenschaften:

- nur Logging
- Hinweis in Audit / Debug / TUI
- keine harte Modusänderung
- keine Tool-Sperre
- keine Blockade

Typische Advisory-Signale:

- `rds_v2` steigt deutlich
- `cost_total` ist erhöht, aber noch im Rahmen
- `risk_total` ist erhöht, aber `actionable_risk` noch unter Soft-Limit
- `utility` fällt gegen null
- `score_code` oder `score_robustheit` ist schwächer als `score_fach`

Advisory-Zweck:

- Beobachtbarkeit
- Kalibrierung
- Diagnose
- spätere Regelableitung

---

### 4.2 Soft-Control

Soft-Control passt das Verhalten an, ohne die Anfrage hart zu blockieren.

Eigenschaften:

- kontrollierte Verhaltensänderung
- reduzierte Exploration
- sichere Tool-Auswahl
- engere Prompt-Führung
- optionale Retry-/Repair-Strategien

Typische Soft-Control-Maßnahmen:

- Kontextfenster verkleinern
- Memory prunen
- Tool-Nutzung reduzieren
- nur sichere Tools erlauben
- Judge verpflichtend machen
- Modell wechseln
- Retry mit engerem Systemprompt
- Antwortmodus von "explorativ" auf "konservativ" setzen
- Code nur noch mit stärkerer Validierung zulassen

Soft-Control-Ziel:

- Risiko senken
- Kosten begrenzen
- Antwortqualität stabilisieren
- ohne unnötige harte Blockaden auszulösen

---

### 4.3 Hard-Control

Hard-Control greift nur bei klaren, belastbaren, kalibrierten Fällen.

Eigenschaften:

- blockierend
- deterministisch
- nur bei eindeutigem Grenzfall
- Audit-pflichtig

Typische Hard-Control-Maßnahmen:

- Tool-Aufruf blockieren
- Agentenmodus blockieren
- Request abbrechen
- sichere Ersatzantwort erzeugen
- nur Diagnose-/Erklärmodus zulassen

Hard-Control darf nur ausgelöst werden, wenn:

- Policy-/Safety-Regeln verletzt sind
- `actionable_risk` über `hard_max` liegt
- Kosten weiter steigen, aber `utility` negativ bleibt
- harte technische oder sicherheitsrelevante Bedingungen verletzt werden

---

## 5. Entscheidungslogik

### 5.1 Laufzeitmetriken

Verwendete Primärmetriken:

- `cost_total`
- `rds_v2`
- `risk_total`
- `actionable_risk`
- `utility`

Interpretation:

- `cost_total`: wie teuer war der aktuelle Denkpfad?
- `rds_v2`: wie tief/verzweigt/instabil ist das aktuelle Reasoning?
- `risk_total`: diagnostisches Gesamtrisiko
- `actionable_risk`: direkt steuerbares Risiko
- `utility`: bringt der nächste Schritt noch echten Nutzen?

---

### 5.2 Ergebnis-Scores

Verwendete Sekundärmetriken:

- `score_fach`
- `score_code`
- `score_robustheit`
- `score_gesamt`

Interpretation:

- `score_fach`: fachliche Korrektheit
- `score_code`: technische/logische Qualität
- `score_robustheit`: Fehlerverhalten / Validierung / Stabilität
- `score_gesamt`: zusammengefasste Ergebnisnote

---

## 6. Standard-Regeln

### Regel H1 - Advisory bei erhöhtem Reasoning
Wenn `rds_v2` über Beobachtungsschwelle liegt, aber `actionable_risk <= soft_max`, dann:
- `control_mode = "advisory"`
- Audit-Hinweis schreiben
- keine harte Verhaltensänderung

### Regel H2 - Soft-Control bei erhöhtem steuerbarem Risiko
Wenn `actionable_risk > soft_max` und `actionable_risk <= hard_max`, dann:
- `control_mode = "soft"`
- sichere Tool-Reduktion aktivieren
- Judge verpflichtend machen
- explorativen Modus reduzieren

### Regel H3 - Hard-Control bei kritischem Risiko
Wenn `actionable_risk > hard_max`, dann:
- `control_mode = "hard"`
- Agentenmodus blockieren
- unsichere Tools blockieren
- sichere Fallback-Antwort verwenden

### Regel H4 - Soft-Control bei negativer Utility
Wenn `utility < 0` und `cost_total` weiter steigt, dann:
- `control_mode = "soft"`
- Reasoning prunen
- Kontext reduzieren
- weitere Exploration abbremsen

### Regel H5 - Ergebnisbasierter Repair
Wenn `score_fach <= 3` aber `score_code >= 4` oder `score_robustheit >= 4`, dann:
- kein Hard-Block
- stattdessen Soft-Control / Repair-Loop
- Prompt enger fassen
- gezielte Verbesserung anfordern

### Regel H6 - Harte Ergebnisintervention
Wenn `score_fach >= 5`, dann:
- Ergebnis als fachlich kritisch markieren
- keine direkte Auslieferung ohne Repair / Judge
- Hard-Control nur dann, wenn der Output in einen riskanten Bereich fällt

---

## 7. Entscheidungs-Matrix

| Zustand | Aktion |
|---|---|
| niedriges Risiko, positive Utility | normal weiter |
| mittleres Risiko, positive Utility | advisory |
| erhöhtes `actionable_risk` | soft-control |
| hohes `actionable_risk` | hard-control |
| negative Utility, steigende Kosten | soft-control / prune |
| gutes Fachergebnis, schwacher Code | repair |
| schlechtes Fachergebnis | judge / repair / block je nach Risiko |

---

## 8. Prioritätsregeln

Bei Konflikten gilt:

1. harte Policy-/Safety-Regeln zuerst
2. `actionable_risk` vor `risk_total`
3. negative `utility` vor weiterem Explorieren
4. `score_fach` wichtiger als `score_code`
5. `score_code` und `score_robustheit` lösen bevorzugt Repair statt Block aus
6. Hard-Control nur bei klarer Kalibrierung

---

## 9. Empfohlene Betriebsphasen

### Phase 1 - Observe
- nur messen
- keine Blockade
- Advisory-Flags loggen
- Score-System parallel auswerten

### Phase 2 - Advise
- Advisory sichtbar machen
- Soft-Control nur vorsichtig zuschalten
- noch keine aggressive Laufzeitsteuerung

### Phase 3 - Soft-Control
- Kontext-Pruning
- Tool-Reduktion
- Judge-Pflicht
- Repair-Loops aktivieren

### Phase 4 - Hard-Control
- nur für kalibrierte Hochrisiko-Fälle
- Agenten-/Tool-Blockaden
- sichere Fallback-Pfade

---

## 10. Empfohlene technische Felder

```json
{
  "hybrid_control": {
    "control_mode": "advisory",
    "trigger_reasons": [
      "rds_high",
      "utility_falling"
    ],
    "actions": [
      "log_audit",
      "show_debug_flag"
    ],
    "thresholds": {
      "soft_max": 5.0,
      "hard_max": 8.0
    }
  }
}
```

---

## 11. TODO - Hybrid Control Umsetzung

Statuslogik fuer dieses Dokument:

- [x] Pre-Decision-Metriken (`cost_total`, `rds_v2`, `risk_total`, `actionable_risk`, `utility`) definiert
- [x] Post-Result-Scores (`score_fach`, `score_code`, `score_robustheit`, `score_gesamt`) definiert
- [x] Steuerstufen Advisory / Soft-Control / Hard-Control beschrieben
- [x] Prioritaetsregeln und Betriebsphasen dokumentiert
- [x] Technisches Feldschema fuer `hybrid_control` dokumentiert

Closed-Loop Integration:

- [x] Automatische Rueckkopplung von Post-Result-Scores in die naechste Laufzeitentscheidung implementieren (v1: session-basiertes Score-Feedback auf `policy_risk`/`context_entropy`)
- [x] Regel definieren: Wann `score_fach` den naechsten Control-Mode direkt verschaerft (v1: `fach >= 5 -> soft`, `fach >= 6` mit `block|revise` -> `hard` als mode floor)
- [x] Regel definieren: Wann `score_code`/`score_robustheit` bevorzugt Repair statt Block triggern (v1: bei `fach <= 3` und `code >= 5` oder `robustheit >= 5` -> `repair_preferred`, mode floor mindestens `soft`)
- [x] Mapping-Tabelle bauen: Score-Lagen -> konkrete Soft-Control-Aktionen (v1: strukturierte `actions`-Liste in `hybrid_control`/`math_signals`)
- [x] Session-basierte Lern-/Trendlogik fuer wiederholt schwache Scores aufsetzen (v1: Session-Historie, `trend_weak_score_count`, `repeated_weak_scores`)

Orchestrator/Judge Integration:

- [x] `judge_decision_post` explizit in `hybrid_control.trigger_reasons` uebernehmen
- [x] `validation_math_signals` und `validator_score` in einem einheitlichen Decision-Objekt zusammenfuehren (`decision_context` in Trace + Validation-Result)
- [x] Deterministische Konfliktaufloesung implementieren (Policy > Hard Risk > Utility > Score)
- [x] Konfliktaufloesung maschinenlesbar exponieren (`triggered_laws`, `conflict_resolution.winning_law`, `overridden_laws`, Priority/Weight)
- [x] Retry-/Repair-Pfade mit eindeutigen Abbruchkriterien harmonisieren (`retry_control` mit `strategy`, `attempt_allowed`, `stop_reason`)

Audit und Betrieb:

- [x] Audit-Event erweitern: `control_mode_before`, `control_mode_after`, `decision_delta`
- [x] Decision-Explanation-Layer: `decision_explanation` mit `primary_reason`, `secondary_reasons`, `supporting_metrics`, `decision_confidence`, `decision_trace` in `validation_result`, `execution_trace` und `run_debug`
- [x] Dashboard/TUI-Sicht: Closed-Loop-Verlauf pro Run/Session anzeigen (v1.0: Textual app mit interaktivem Threshold-Editor, Validation, 29 Tests)
- [x] Schwellwerte regelmaessig per Runtime-Audit kalibrieren und versionieren (v1: `Settings.reasoning_threshold_profile()` mit `version`/`source` in `validation_math_signals` + runtime-metrics)
- [x] Canary-Phase mit nur Soft-Control-Rueckkopplung fahren, Hard-Control unveraendert lassen (v1: `REASONING_SCORE_FEEDBACK_CANARY_SOFT_ONLY` klemmt score-bedingtes `mode_floor` auf `soft`, Hard-Risk/Policy-Pfade bleiben aktiv)
- [x] Outcome-Guarded Rollback fuer Closed-Loop aktivieren (bei verschlechterter Decision/Confidence/Risk Rueckfall auf Baseline-Thresholds, payload: `threshold_adaptation.rolled_back`)

Tests:

- [x] Integrationstest: hoher Pre-Risk + guter Post-Score -> keine unnoetige Verschaerfung (`test_high_pre_risk_good_post_score_no_over_escalation`)
- [x] Integrationstest: niedriger Pre-Risk + schlechter Post-Score -> Repair-Loop statt sofortiger Block (`test_code_and_robustheit_can_prefer_repair_mode`)
- [x] Integrationstest: wiederholt schlechter `score_fach` -> schrittweise Control-Mode-Anhebung (`test_repeated_weak_fach_stepwise_mode_escalation`)
- [x] Regressionstest: RDS bleibt diagnostisch und schaltet nicht direkt hart (`test_rds_high_actionable_risk_below_threshold_stays_advisory`)
- [x] E2E-Test: vollstaendiger Closed-Loop ueber mindestens 3 Turns mit nachvollziehbarer Delta-Entscheidung (`test_e2e_three_turn_closed_loop_delta_direction_chain`)