# Architektur Delta-Notiz (Archiv)

Status: historische Delta-Zusammenfassung, aktualisiert am 2026-04-19.

## Zweck dieser Datei

Diese Datei war eine ergaenzte Zwischenfassung waehrend der Architekturmigration.
Sie bleibt als kurze Archiv-Notiz erhalten, ist aber keine kanonische Gesamtarchitektur
mehr.

Kanonische Referenzen:

- `docs/ARCHITECTURE.md`
- `docs/CURRENT_STATUS_OVERVIEW_2026-04-14.md`
- `docs/BACKEND_ANALYSIS_2026-04-19.md`

## Historischer Delta-Kern

Die damals hervorgehobenen Deltas sind inzwischen weitgehend in den Hauptdokus und im
Code aufgegangen:

- Context-Strategy als zentrale Routing- und Kontextschicht
- klare Trennung von `context` (Scope) und `memory` (Langzeit)
- Embedding als dedizierter Servicepfad
- service-orientierte Memory-Adaptergrenze

## Context Strategy (Archiv-Abriss)

```python
if query_type == "simple":
    use_tool()
elif query_type == "context":
    scope = resolve_scope(session, run, file, symbol, time_window)
    context = context_search(query, scope)
elif query_type == "memory":
    memory = memory_search(query)
else:
    context = build_full_context()
```

Leitregel aus dieser Delta-Phase:

- Scope-basierte Kontextsuche darf nicht als globaler Similarity-Lookup betrieben werden.

## Heute gueltig fuer Planung

- Ist-Stand und Restthemen werden in den zentralen Status-/Fachdokumenten gepflegt.
- Diese Datei dient nur noch als kompaktes Migrationsprotokoll.

## Nachgezogen am 2026-04-29 (Delta-Hinweis)

Folgende inzwischen implementierte Runtime-Aspekte sind in der kanonischen Architektur
explizit dokumentiert und hier als Synchronisationshinweis festgehalten:

- explainability/decision_path-Kompatibilitaet im Validation-Payload
- maschinenlesbare Law-Konfliktaufloesung (`triggered_laws`, `conflict_resolution` mit Winner/Overrides inkl. Priority/Weight)
- adaptive Runtime-Thresholds mit outcome-guarded Rollback (`threshold_adaptation.rolled_back`, `reason=outcome_degraded`)

Kanonische Detailquelle:

- `docs/ARCHITECTURE.md`
