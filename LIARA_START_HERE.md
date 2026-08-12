# LIARA — Start Here

Stand: 2026-08-11

## Zweck

Dies ist der kanonische Einstieg fuer Menschen und KI-Assistenten, die LIARA
zum ersten Mal analysieren oder weiterentwickeln. Das Paket gibt allen
Instanzen dieselbe Ausgangsbasis. Es schreibt keine Interpretation vor; es
verhindert, dass unterschiedliche Modelle nur wegen unterschiedlich
vollstaendigem Kontext zu anderen Ergebnissen kommen.

## Verbindliche Lesereihenfolge

1. [`LIARA_CURRENT_STATE.md`](LIARA_CURRENT_STATE.md) — was nachweislich
   implementiert, teilweise implementiert, geplant oder abgeloest ist.
2. [`LIARA_ARCHITECTURE_MAP.md`](LIARA_ARCHITECTURE_MAP.md) — Komponenten,
   Grenzen, Fluesse und Rueckkopplungen.
3. [`LIARA_TERMS_AND_PRINCIPLES.md`](LIARA_TERMS_AND_PRINCIPLES.md) —
   gemeinsames Vokabular und architektonische DNA.
4. [`LIARA_NEXT_PRIORITIES.md`](LIARA_NEXT_PRIORITIES.md) — naechste Aufgaben
   mit pruefbaren Akzeptanzkriterien.
5. Erst danach die vertiefenden Quellen, auf die diese Dateien verweisen.

## Quellenhierarchie

Bei Widerspruechen gilt:

```text
aktiver Quellcode + aktuelle Tests + beobachtete Runtime
> docs/00_index.md und datierte aktive Service-Dokumentation
> dieses Einstiegspaket
> historische Spezifikationen, Snapshots und Buildberichte
> Backups, Artefakte und generierte Kopien
```

Die verbindliche Pfadabgrenzung steht in
[`docs/AUDIT_SOURCE_OF_TRUTH.md`](docs/AUDIT_SOURCE_OF_TRUTH.md). Insbesondere
sind `backups/`, `artifacts/`, `build/`, `dist/` und Logs keine primaeren
Codequellen.

## Arbeitsregel fuer neue Assistenten

Vor einer Aenderung:

1. SOLL und IST trennen.
2. Behauptungen am aktiven Codepfad pruefen.
3. Betroffene Contracts, Datenfluesse und Tests benennen.
4. Bestehende Nutzerarbeit und fachfremde Dateien unangetastet lassen.
5. Architektur-, Governance- und Sicherheitsentscheidungen nicht
   stillschweigend treffen.
6. Eine Mutation erst nach beobachtetem Zustand, Test oder Hash-Evidenz als
   erfolgreich bezeichnen.

## Gemeinsamer Orientierungsauftrag

Wer verschiedene Modelle miteinander vergleichen will, kann jedem Modell
nach dem Lesen exakt denselben Auftrag geben:

> Beschreibe LIARA in deiner eigenen Systemperspektive. Unterscheide
> Komponenten, Beziehungen, Fluesse, Kontrollgrenzen und Entwicklungsstand.
> Nenne drei besondere Staerken, drei reale Risiken und die zwei
> wirkungsvollsten naechsten Schritte. Trenne belegte Tatsachen klar von
> Schlussfolgerungen. Veraendere noch keine Dateien.

Damit wird die Interpretation verglichen — nicht die Vollstaendigkeit des
bereitgestellten Kontextes.

## Schnellzugriffe

- Living Architecture Map:
  [`http://127.0.0.1:3001/architecture`](http://127.0.0.1:3001/architecture)
- Ausfuehrliche Uebergabe: [`docs/00_index.md`](docs/00_index.md)
- Architektur: [`docs/01_architektur/liara-overview.md`](docs/01_architektur/liara-overview.md)
- Services: [`docs/02_services/`](docs/02_services/)
- Tests: [`docs/07_tests/test-overview.md`](docs/07_tests/test-overview.md)
- Sicherheit: [`docs/08_security/security-boundaries.md`](docs/08_security/security-boundaries.md)
- Runtime: [`docs/09_reference/runtime-reference.md`](docs/09_reference/runtime-reference.md)
