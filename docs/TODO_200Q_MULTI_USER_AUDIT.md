# TODO 200Q Multi-User Audit

Stand: 2026-04-22
Quelle: Auswertung des 200Q-Multi-User-Benchmarks (Live-Ausschnitt + aktueller 200er Script-Stand)

## Ziel
Stabilen 200-Fragen-Live-Run mit belastbarer Summary erreichen und die Hauptprobleme (Recall, Easy-Latenz) systematisch reduzieren.

## Prioritaet P0 (direkt)

- [ ] 200Q-Live-Run mit aktueller Script-Version vollstaendig erneut ausfuehren
  - Datei: scripts/benchmark_200q_multi_user_audit.py
  - Erwartung: sauberer Abschluss inkl. JSONL + Summary-JSON
  - Akzeptanzkriterium: kein Laufzeitfehler am Ende, Reports werden geschrieben

- [ ] Ergebnisartefakte des neuen Runs sichern und referenzieren
  - Zielpfad: logs/tests/benchmark_200q_*.jsonl und logs/tests/benchmark_200q_*_summary.json
  - Akzeptanzkriterium: Timestamp, Commit-Hash, API-Base-URL, Laufdauer dokumentiert

## Prioritaet P1 (Recall-Qualitaet)

- [ ] Recall-Fehler clusterweise auswerten
  - Fokus: memory_recall Turns mit MISS pro Nutzer und Topic
  - Akzeptanzkriterium: Top 5 Fehlermuster inkl. Beispielturns dokumentiert

- [ ] Recall-Prueflogik robuster machen
  - Synonyme/Varianten aufnehmen (z. B. Zahlwort vs. Ziffer, Flexionen)
  - Optional: einfache Normalisierung vor Keyword-Match
  - Akzeptanzkriterium: weniger False-Negatives bei unveraenderter Antwortqualitaet

- [ ] Memory-Retrieval-Pfad bei Recall-Fragen gezielt pruefen
  - Trace fuer betroffene Turns analysieren (welche Erinnerung wurde geladen / nicht geladen)
  - Akzeptanzkriterium: mindestens 3 reproduzierbare Ursachen identifiziert oder ausgeschlossen

## Prioritaet P1 (Easy-Latenz)

- [ ] Easy-Latency-Ueberschreitungen separat messen
  - SLA aktuell: <= 45s fuer easy
  - Akzeptanzkriterium: Verteilung (P50/P95/Max) fuer easy-turns dokumentiert

- [ ] Entscheidungsvorlage fuer SLA erstellen
  - Option A: Easy-SLA auf 50s anheben
  - Option B: Routing/Prompt fuer leichte Fragen beschleunigen
  - Akzeptanzkriterium: klare Empfehlung mit Vor-/Nachteilen

## Prioritaet P2 (Benchmark-Hygiene)

- [ ] Benchmark-Summary um Fehlertypen-Topliste erweitern
  - Beispiele: recall_miss, latency>45.0s, stream_incomplete
  - Akzeptanzkriterium: Summary zeigt pro Fehlertyp Count + Prozent

- [ ] Reproduzierbare Run-Profile dokumentieren
  - Profile: full-live, dry-run, user-filter
  - Akzeptanzkriterium: 3 lauffaehige Kommando-Beispiele in Doku

## Definition of Done

- [ ] Ein neuer 200Q-Live-Run ist ohne Script-Fehler abgeschlossen
- [ ] Reports sind abgelegt und auswertbar
- [ ] Recall-MISS-Quote ist messbar verbessert oder Ursachen sind klar dokumentiert
- [ ] Easy-Latenz-Entscheidung (SLA anpassen vs. Performance-Optimierung) ist getroffen
