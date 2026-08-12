# MIRKO Mathematik - Konsolidierte Doku

Diese Datei fasst die Inhalte aus MIRKO SPIEL MIT MATHEMATIK und MIRKO_MATHE_2 in einer gemeinsamen, aktuellen Referenz zusammen.

## 1) Ziel und Architektur

LIARA bewertet Reasoning nicht nur heuristisch, sondern ueber ein explizites mathematisches Modell fuer Kosten, Unsicherheit, Risiko und Nutzen.

Leitplanken:

- Komplett lokaler Pfad fuer Mirko-Mathematik (API, Memory, Embeddings, Retrieval, Julia-Compute).
- Python ist Orchestrierung/Integration (IO, Contracts, Runtime-Steuerung).
- Julia ist primaerer Rechenkern fuer komplexe Metriken.
- Python-Fallback ist robust vorhanden, falls Julia/Bridge nicht verfuegbar ist.
- Audit/Judge/TUI konsumieren dieselben Metriken konsistent ueber beide Compute-Pfade.

Rechenpfad:

$$
API \rightarrow Orchestrator \rightarrow lokaler\ Julia\-Bridge\-Pfad \rightarrow Julia
$$

## 2) Gemeinsames Kernmodell

Zustand und Aktionen:

$$
s = (c, m, g)
$$

- $c$: aktiver Kontext
- $m$: relevantes Memory-Subset
- $g$: aktuelles Ziel
- Aktionen: Tool, Memory-Operation, interner Reasoning-Schritt

Kosten/Nutzen:

$$
C(a) = \alpha \cdot depth + \beta \cdot tokens + \gamma \cdot tools + \delta \cdot entropy
$$

$$
U(a) = goal\_progress - C(a)
$$

$$
a^* = \arg\max_a U(a) \quad \text{mit} \quad C(a) \le C_{\max}
$$

Reasoning-Kosten (diagnostisch + steuernd):

$$
C_{total} = C_d + C_m + C_t + C_e
$$

RDS v2 (Komplexitaet/Depth):

$$
RDS = \log_2(1 + D \cdot B) + \lambda \cdot H
$$

Risiko:

$$
R_{total} = w_p R_p + w_u R_u + w_c R_c
$$

$$
R_{actionable} = w_p R_p + w_u R_u
$$

Gating:

- $R_{actionable} > R_{hard\_max}$: hard block
- $R_{actionable} > R_{soft\_max}$: soft limit/safe mode
- sonst normaler Modus

## 3) Ergaenzte Mathe-Bausteine (aus MIRKO_MATHE_2)

Die folgenden Bausteine waren als Ergaenzung vorgeschlagen und sind inzwischen umgesetzt:

- Bayes/Posterior-Updates
- Kalman-aehnliches Belief-Tracking
- Graph-Metriken: Clustering, Modularity, Shortest Path
- Stabilitaetsheuristik fuer Divergenz/Loops
- Regularisierung (L1/L2-Charakter)
- Varianz/Confidence-Metriken
- Temporal Discounting
- Multi-Objective/Pareto-Entscheidung
- Information Gain (IG)
- Confidence-Weighted Utility

## 4) Umsetzungsstand (kompakt)

Phasenstatus:

- Phase 0 (Leitplanken/Contracts/Audit-Felder): erledigt
- Phase 1 (Belief, Bayes, Kalman, Varianz): erledigt
- Phase 2 (IG, weighted utility, discounting): erledigt
- Phase 3 (Graph, Stabilitaet, Regularisierung): erledigt
- Phase 4 (Multi-Ziel-Entscheidung): erledigt
- Phase 5 (Orchestrator/Judge/Retry-Integration): erledigt
- Phase 6 (Tests, Kalibrierung, Betrieb): erledigt

Explizite Nachweise aus den letzten Runs:

- Live-Stream-Regression: 2 passed
- Julia-Paritaet mit aktivem Flag RUN_JULIA_PARITY_TESTS=1: 4 passed

## 5) Betriebsprinzip

- Erst beobachten, dann bewerten, dann begrenzen, dann erzwingen.
- Kalibrierung ueber Runtime-Audit-Report und versionierte Threshold-Empfehlungen.
- Keine Auslagerung dieses Mathepfads an externe Dienste.

## 6) Ergebnis

Mirko-Mathematik in LIARA ist nun als einheitlicher, lokal kontrollierter, auditierbarer und produktionsnah verifizierter Stack dokumentiert.

## 7) Decision Explanation Layer

Der Hybrid-Control-Stack erzeugt jetzt zusaetzlich eine deterministische Entscheidungsbegruendung pro Run.

- Output: `validation_result.decision_explanation`
- Audit: auch in `execution_trace.validation.metadata`, `execution_trace.complete.metadata` und `run_debug`
- Prioritaet: policy -> hard risk -> soft risk -> utility -> score -> normal operation

Details siehe [docs/DECISION_EXPLANATION_LAYER.md](docs/DECISION_EXPLANATION_LAYER.md).
