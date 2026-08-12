# TODO: Plotting im Chat + Anpassungen an letzte Neuerungen

## Zielbild
- Diagramme (zunaechst PNG) koennen durch LIARA erzeugt werden.
- Diagramme werden im Chat-Response als Artefakte mitgeliefert.
- Diagramme werden in der UI direkt im Chat angezeigt (mindestens GTK), in Text-UI mit Link/Metadaten.
- Bestehende Neuerungen (Decision Explanation Layer, Validation-Metadaten, Streaming-Final-Payload) bleiben konsistent.

## Phase 1 - Contracts und API Payload erweitern
- [x] In `services/contracts/service_boundaries.py` ein strukturiertes Artefakt-Schema einfuehren.
- [x] `ChatResponse` um `artifacts` erweitern (z. B. Liste mit `kind`, `mime_type`, `title`, `url`, `width`, `height`, `source_tool`).
- [x] Rueckwaertskompatibilitaet sichern: bestehende Felder bleiben unveraendert.
- [x] API-Antwort in `services/api/app.py` so erweitern, dass Artefakte aus dem Orchestrator sauber durchgereicht werden.

### Akzeptanzkriterien Phase 1
- [x] `/chat` liefert bei vorhandenem Diagramm ein befuelltes `artifacts`-Feld.
- [x] Ohne Diagramm ist `artifacts` leer oder fehlt (klar dokumentiert, einheitliches Verhalten).

## Phase 2 - Plot-Tool (MVP) implementieren
- [x] Neues Tool anlegen: `plot_chart` (Linie/Balken fuer Start).
- [x] Eingaben: Titel, x/y-Daten, Chart-Typ, optionale Beschriftungen.
- [x] Ausgabe: gespeicherte PNG-Datei im Session-Sandbox-Kontext + Artefakt-Metadaten.
- [x] Sichere Dateinamen und Session-Scoped Ablage verwenden.
- [x] Tool in Registry aufnehmen.

### Akzeptanzkriterien Phase 2
- [x] Tool-Aufruf erzeugt valide PNG-Datei.
- [x] Tool-Rueckgabe enthaelt alle benoetigten Artefakt-Metadaten.

## Phase 3 - Sicherer Datei-Read fuer Artefakte
- [x] In `services/api/app.py` einen sicheren Read-Endpunkt fuer Artefakte bereitstellen (Session-Scoped, path-validiert).
- [x] Zugriff nur innerhalb erlaubter Sandbox-Pfade.
- [x] Passende Fehlercodes fuer "nicht gefunden", "verboten", "ungueltiger Pfad".

### Akzeptanzkriterien Phase 3
- [x] Diagramm-URL aus `artifacts` ist ueber API abrufbar.
- [x] Path-Traversal wird abgewehrt.

## Phase 4 - Streaming erweitern
- [x] `/chat/stream` um Artefakt-Event erweitern (z. B. `event: artifact`).
- [x] `event: final` enthaelt vollstaendige Artefaktliste.
- [x] Heartbeat/Progress-Events bleiben kompatibel.

### Akzeptanzkriterien Phase 4
- [x] Bei Diagramm-Antwort kommt waehrend oder spaetestens im finalen Event ein Artefakt an.

## Phase 5 - Frontend Integration

### GTK UI
- [x] In `frontend/WMTool-Liara/src/liara_window.c` Final-Payload-Parser um `artifacts` erweitern.
- [x] Bild-Artefakte inline unter der Assistant-Nachricht rendern.
- [x] Fallback bei Ladefehler (z. B. Platzhalter + Link) implementieren.

### Textual UI
- [x] In `frontend/tex-ui/textual_chat/client.py` und App-Rendering Artefakte aus Payload anzeigen.
- [x] Fuer Terminal: Link + Titel + Typ + Groesse (kein Bildzwang).

### Akzeptanzkriterien Phase 5
- [x] GTK zeigt PNG direkt im Chatverlauf.
- [x] Textual zeigt klickbaren/lesbaren Artefakt-Hinweis.

## Phase 6 - Anpassung an letzte Neuerungen (Sofort mitziehen)
- [x] `decision_explanation` bleibt weiterhin unter `metadata.validation.decision_explanation` unveraendert verfuegbar.
- [x] `execution_trace` enthaelt weiterhin Validation/Complete-Metadaten inkl. Decision-Explanation.
- [x] `run_debug` bleibt vollstaendig; Artefakte werden zusaetzlich, nicht ersetzend, angebunden.
- [x] Event-Struktur aus neuer Streaming-Logik bleibt stabil (`progress`, `heartbeat`, `chunk`, `final`, `done`).
- [x] API-Dokumentation/Beispiele um Artefakt-Payload und Decision-Explanation gemeinsam erweitern.

### Akzeptanzkriterien Phase 6
- [x] Bestehende Tests zur Decision-Explanation laufen unveraendert weiter.
- [x] Neue Artefakt-Funktionalitaet bricht keine vorhandenen Response-Felder.

## Tests (verpflichtend)
- [x] Unit: Contract-Serialisierung fuer `ChatResponse.artifacts`.
- [x] Unit: Plot-Tool erzeugt Datei + Metadaten.
- [x] Unit: Security-Checks fuer Artefakt-Read-Endpunkt.
- [x] Integration: `/chat` mit Diagramm liefert Text + Artefakt.
- [x] Integration: `/chat/stream` liefert Final-Payload mit Artefakten.
- [x] Regression: bestehende Tests fuer Orchestrator-Validation und Decision-Explanation bleiben gruen.

## Rollout
- [x] Feature-Flag fuer Plotting-Antworten (optional, empfohlen): `PLOTTING_TOOLS_ENABLED` implementiert.
- [x] Zuerst intern aktivieren, danach stufenweise freischalten: Rollout-Plan in `PLOTTING_ROLLOUT_PLAN.md`.
- [x] Logging fuer Artefakt-Erzeugung, Dateigroesse und Render-Fehler beobachten: Instrumentation in `plot_chart.py` und `app.py`.

## Definition of Done
- [x] End-to-End: Nutzer fragt nach Diagramm, LIARA erzeugt Plot und zeigt ihn im Chat an.
- [x] API liefert Artefakt-Daten stabil in `/chat` und `/chat/stream`.
- [x] Decision-Explanation und neue Hybrid-Control-Metadaten bleiben voll kompatibel.
