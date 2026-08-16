# Services: Frontends, CLI und TUI

Stand: 2026-07-14

## Native Frontends

Unter `frontend/` liegen mehrere UI-Staende:

- `WMTool-Liara`: aktive C/GTK4 Desktop-GUI fuer `liara-api`
- `gtk-ui`: frueherer/native GTK4-Prototyp
- `server-manager`: ausgelagerter Server-Manager
- `admin_tui`, `qt-ui`, `tex-ui`, `web-ui`: weitere UI-/Experimentbereiche
- `*-backup-*`: Backup-Staende

`frontend/README.md` ist veraltet formuliert und sagt noch, es gebe keine aktive Frontend-Implementierung. Der aktuellere Stand ist `frontend/WMTool-Liara/README.md`.

## WMTool-Liara

Aktiver Scope laut README:

- `POST /chat`
- `POST /chat/stream`
- `GET /history`
- `GET /tools`
- `POST /tools/{tool_name}/invoke`
- `GET /health`
- `GET /health/backends`
- Explorer-Seite ueber geschuetzte Sys-/Tool-Ausfuehrung

Default API Base URL:

```text
http://127.0.0.1:8010
```

Build/Package erfolgt ueber MSYS2, Meson, Ninja und `package.ps1`.

## CLI/TUI

Python-seitig existieren:

- `services/cli/main.py`
- `services/cli/textual_chat/*`
- `services/tui/admin_console.py`
- `services/tui/liara_shell.py`
- `services/tui/memory_inspector.py`
- `services/tui/worker_monitor.py`
- `services/tui/sys_audit_tui.py`

### Argumentbasierter API-Testclient

`services/cli/main.py` ist implementiert und als direkter, skriptfaehiger
Client vor `liara-api` nutzbar. Der aktuelle Datenfluss ist:

```text
Mensch / Codex / Copilot / Testskript
-> python -m services.cli.main <command> [args]
-> HTTP an liara-api
-> Orchestrator / Tools / Validator
-> formatierte Terminalausgabe
```

Implementierte Subcommands sind `chat`, `stream`, `history`, `session`,
`repl`, `chat-ui` und `health`. `chat` verwendet `POST /chat`, `stream`
verwendet `POST /chat/stream` via SSE. Basis-URL, Timeout, Session, User,
Tokenlimit sowie bevorzugter Provider und bevorzugtes Modell sind ueber
Argumente beziehungsweise Umgebungsvariablen steuerbar.

Verifizierter Stand am 2026-07-14:

- `python -m services.cli.main --help` funktioniert.
- `tests/unit/test_cli.py`: 37 Tests bestanden.
- `python -m services.cli.main health` und der direkte API-Healthcheck sind
  gegen die laufende lokale `liara-api` erfolgreich.
- `--output json` liefert fuer `health`, `chat`, `stream`, `history` und
  `session` genau ein JSON-Dokument auf stdout (`liara.cli.v1`).
- JSON-Fehler werden auf stderr ausgegeben; Rich-/ANSI-Ausgabe bleibt aus dem
  Maschinenmodus entfernt.
- Nicht-ASCII-Zeichen in Antworten und Metadaten werden fuer die Ausgabe
  ASCII-sicher escaped und nach dem JSON-Parsing verlustfrei rekonstruiert;
  damit bleibt der Vertrag auch unter der Windows-Codepage `cp1252` schreibbar.
- `stream --output json` aggregiert die SSE-Ausgabe zu einem Abschlussdokument.
  Ein Wechsel von Stream auf Chat wird unter `_cli.fallback` und
  `_cli.fallback_reason` sichtbar.
- `--fail-on-validation` uebersetzt Validator-Entscheidungen in stabile
  Exitcodes statt einen HTTP-2xx pauschal als fachlichen Erfolg zu behandeln.
- `liara-cli = services.cli.main:main` ist als Konsolen-Einstieg in
  `pyproject.toml` definiert. `python -m services.cli.main` bleibt kompatibel.

### Maschinenlesbarer CLI-Zugang fuer Codex, Copilot und CI

Globale Optionen muessen vor dem Subcommand stehen:

```powershell
.\.venv\Scripts\python.exe -m services.cli.main --output json health
.\.venv\Scripts\python.exe -m services.cli.main --output json chat "Pruefe LIARA" --session-id codex-test
.\.venv\Scripts\python.exe -m services.cli.main --output json --fail-on-validation stream "Pruefe LIARA"
```

Exitcodes:

- `0`: technischer Erfolg; ohne `--fail-on-validation` keine fachliche Aussage
  ueber Warn-/Revisionsentscheidungen
- `2`: ungueltige CLI-Nutzung oder nicht unterstuetzter Maschinenmodus
- `3`: HTTP-/Transportfehler oder nicht erreichbare API
- `4`: Validator `warn`/`revise` bei aktivem `--fail-on-validation`
- `5`: Validator `block` oder nicht gesunder Health-Status
- `130`: Benutzerabbruch

Noch offen: Ein eigener installierter `liara-cli`-Befehl wird erst nach einer
erneuten Paketinstallation im jeweiligen Environment erzeugt. Der Modulaufruf
benoetigt keine Neuinstallation.

### Aktuell verwendetes Textual-Frontend

Der am 2026-07-14 laufende interaktive Client ist
`frontend/tex-ui/main.py`. Er bietet Chat/Stream, lokalen Transcript-Cache,
einen UNC-basierten Explorer fuer `/home/liara`, strukturierte `/sys`-Aufrufe
und sichtbare Write-Verifikation. Der Default fuer `max_tokens` ist 32768.

Start:

```powershell
.\.venv\Scripts\python.exe .\frontend\tex-ui\main.py --base-url http://127.0.0.1:8010 --mode stream
```

Relevante bestehende Doku:

- `docs/TUI_ARCHITECTURE.md`
- `docs/SERVER_MANAGEMENT_GUI.md`

## Aktueller Befund

Die UI-Landschaft ist gewachsen und nicht vollstaendig konsolidiert.
`frontend/tex-ui` ist der aktuell verwendete Client, `WMTool-Liara` der
umfangreichste native GTK/C-Client. Die beiden Pfade sowie alte Prototypen und
Backups besitzen noch keine gemeinsame Release-/Support-Kennzeichnung.

## Web UI: Living Architecture Map

Der Web-Chat unter `frontend/web-ui/src/app/page.tsx` projiziert den aktuellen
`POST /chat/stream`-Contract als zusammenhaengenden Conversation-Run. Neben
Text-Chunks verarbeitet er Progress, Heartbeat, Artifact, Final, Error und Done.
Der sichtbare Run-Nachweis umfasst Run-ID, Inference-Provider/-Modell, TTFT,
Generierungszeit, Context-Modus und -Quellen, Validator-/Decision-Explanation,
Reasoning-Metriken, Tool-Aktivitaet sowie pending externe Toolcalls. Artefakte
und TTS bleiben direkt an die jeweilige Assistant-Nachricht gebunden. Laufende
Requests koennen clientseitig abgebrochen werden; Provider-/Modellpraeferenz
und Source-aware Risk Reassessment werden als bestehende `ChatRequest`-Felder
gesendet. Text- und Code-Dateien bis 1 MB werden als strukturierte
`ChatAttachment.text_content`-Eintraege an denselben Request gebunden. Fuer
binaere Uploads erfindet das Web-Frontend keinen parallelen Sondervertrag.

Die Next.js-Weboberflaeche unter `frontend/web-ui` enthaelt jetzt die statisch
prerenderbare Route `/architecture`. Sie bildet die Architektur nicht als
festes Bild, sondern als erweiterbares Datenmodell ab:

```text
architecture-data.ts
-> Komponenten + Reifegrad + Codepfade
-> gerichtete, typisierte Beziehungen
-> System-, Chat-, Workspace- und Kontrollsicht
-> SVG-Diagramm + mobile Listenansicht
-> auswaehlbare Detail- und Beziehungsebene
```

Statuswerte sind `implemented`, `partial`, `prepared`, `planned` und `retired`.
`prepared` markiert Komponenten, die im Repository existieren und verifiziert
funktionieren, aber in keinen laufenden Fluss eingebunden sind (keine
Live-Verdrahtung) -- getrennt von `partial` (echte Teil-Produktion) und
`planned` (noch nicht gebaut).
Beziehungen unterscheiden Datenfluss, Entscheidung, Mutation und Validierung.
Neue Komponenten werden in `src/app/architecture/architecture-data.ts`
eingetragen; die Darstellung generiert Diagramm, Suche, Details und direkte
Beziehungen daraus. Die Route ist aus der Navigation der Hauptseite erreichbar.

Der `Self Observer` ist inzwischen als `partial` markierter Laufzeitknoten
vorhanden. Resource Heartbeat, API-/Backend-Health und persistierte
ai-validator-Pruefevidenz werden zyklisch in einen `SystemStateEnvelope`
verdichtet. Die Karte liest Zustand, Phase und Ruhezyklen ueber
`GET /operations/self-observer`. Der Konsum durch Orchestrator, Dreaming oder
andere Steuerinstanzen bleibt geplant; Findings duerfen nicht durch den
Observer aufgehoben oder als Selbstfreigabe interpretiert werden.

Die zweite Ausbaustufe ergaenzt:

- klickbare Kanten mit Bedeutung, Quelle, Ziel und Contract/Signal;
- Standfilter fuer nur implementiert, Ist plus teilweise sowie Zielbild;
- Trace-Presets fuer `Chat -> Memory`, `Workspace -> Validator` und
  `Finding -> Freigabe`;
- komponentenbezogene Readiness-Claims mit Scope, Umgebung, Pruefzeitpunkt,
  Evidenz und offenen Gates;
- statische Evidenzreferenzen auf Healthchecks, Testbaseline und Build-Historie;
- den `Resource Heartbeat` als teilweise implementierten, realen Laufzeitknoten.

Die dritte Ausbaustufe bindet read-only Live-Evidenz an dieselbe Karte:

```text
GET /health + GET /health/backends
-> API- und Backendzustand

GET /admin/sys-audit/summary
-> SYS-Ereignisse, Writes, Blockierungen und Risiken

GET /tools/sys/governance/proposals
-> Governance-Proposals, Decisions, Invocation-Zustaende und Audit-Referenzen

GET /operations/dreaming
-> Dreaming-/Staging-Status, pending Proposals, Assurance und Quality-Signale

GET /operations/workspace
-> Workspace-Status und Validator-/Governance-/Memory-Artefakte

GET /operations/heartbeat
-> HeartbeatSnapshot, Zustandskurve, Ressourcenhuelle und Signages
```

Die Weboberflaeche aktualisiert diese Daten alle 30 Sekunden und auf explizite
Benutzeranforderung. Drei kompakte Live-Werte stehen oberhalb der Karte;
komponentenspezifische Evidenz erscheint im Detailpanel. API, Context/Memory,
Tools/SYS, Mutation Verification, WSL Workspace, ai-validator, Governance,
Simulation/Dreaming und der bestehende Heartbeat-Teilbaustein erhalten dadurch
reale Zustandssignale.

Beim Governance-Knoten liest die Architecture Map die Gesamtprojektion
read-only. Sie unterscheidet offene Proposals, verbrauchte Autorisierungen,
Policy-Blocks und fehlgeschlagene Invocations. Die zugrunde liegende
Ereignissequenz ist ueber die jeweilige `audit_reference` mit
`GET /tools/sys/governance/events?proposal_id=...` erreichbar. Die Karte kann
weder Decisions treffen noch Invocations starten.
Statische, datierte Evidenz bleibt als Fallback und historischer Nachweis
erhalten.

Der UI-Begriff `Dreaming` beschreibt die sichtbare Systemphase. Die
technische Backend-Grenze dahinter bleibt Staging -> Dreaming-/Consolidation
-> Proposal -> Decision. Die Architekturkarte liest diesen Zustand nur
read-only ueber `GET /operations/dreaming`; sie startet keine Runs und trifft
keine Entscheidungen. Details stehen in `docs/02_services/liara-dreaming.md`.

Die Textual Admin Console nutzt denselben Snapshot. Sie zeigt staged/pending
Counts sowie eine Proposal-Tabelle mit Decision, Assurance-Verdict,
Validator-Job, Findings, Complexity, Quellen-/Relations-Coverage und
Artefaktpfad. Die Detailaktion `v` projiziert zusaetzlich Blockierungs-,
Audit- und Quality-Rohwerte einschliesslich unbedeckter Quellen in den
Eventbereich. Auch diese Oberflaeche bleibt rein lesend.

Bei Auswahl des Heartbeat-Knotens zeigt das Detailpanel bewusst nur einen
verdichteten Systempuls. Die Linie wird aus den zeitlich zusammengehoerigen
Auslastungswerten gebildet: 65 Prozent des jeweiligen Ressourcenpeaks plus
35 Prozent des Mittelwerts. Damit bleibt ein Engpass sichtbar, ohne einzelne
Sensorwerte in der Architekturkarte auszubreiten. Zustand, Trend, Sequenz und
Frische ergaenzen die Linie. Einzelmetriken und Adapterdiagnose sind der
spaeteren administrativen Ansicht vorbehalten. Die Abfrage laeuft nur solange
das Panel ausgewaehlt ist im Zwei-Sekunden-Takt; der globale Kartenstatus
bleibt beim 30-Sekunden-Intervall.

Der Artefaktpfad ist inzwischen WSL-nativ geschlossen. Auf Windows waehlt der
Artifact Store im Auto-Modus den kanonischen Root
`/home/liara/workspace/.liara_artifacts`. Writes laufen als strukturierte
`mkdir`-/`tee`-Aufrufe ueber SYS und gelten erst nach Read-after-write und
SHA-256-Pruefung als erfolgreich. Die Operations-API liest denselben Bestand
ueber den WSL-UNC-Zugang. Alte Dateien unter `C:\home\liara\workspace` bleiben
als Legacy-Bestand unangetastet und werden nicht automatisch migriert.

Readiness ist dabei kein globales Ja/Nein-Feld. Ein Claim gilt nur fuer seinen
angegebenen Scope und seine Umgebung. So kann der `ai-validator` im geprueften
lokalen Pfad `production-capable` sein, ohne LIARA als Gesamtplattform
production-ready zu deklarieren.

Start und Verifikation:

```powershell
cd frontend/web-ui
npm run dev
# http://127.0.0.1:3001/architecture

npm run lint
npm run build
```

Der Produktions-Build vom 2026-07-15 rendert `/architecture` weiterhin als
statische Route. Die Live-Daten werden erst nach der Hydration vom lokalen
LIARA-API-Port 8010 geladen; ein API-Ausfall verhindert daher nicht die Anzeige
der statischen Architektur und ihrer historischen Evidenz.

Port `3000` ist auf dem geprueften Host bereits durch den als NSSM-Dienst
gestarteten Prozess `StandaloneDNA.exe` belegt. Dessen Angular-Oberflaeche ist
nicht das LIARA-Webfrontend und liefert fuer `/architecture` ein `404`.
`npm run dev` und `npm run start` verwenden deshalb fuer LIARA explizit Port
`3001`.

Der aktuell lokal verwendete Python-Servermanager
`server_management_gui.py` bietet fuer `Frontend Web UI` einen eigenen
`Build`-Button. Er fuehrt `npm run build` asynchron aus und streamt die Ausgabe
ins Manager-Log. Der laufende `next start`-Prozess wird dabei nicht automatisch
neu gestartet; die neue `.next`-Ausgabe wird erst durch den separaten
`Restart`-Button aktiviert. Der Contract ist durch
`tests/unit/test_server_management_gui.py` abgedeckt.

Da `run_server_management_gui_msys.cmd` eine MSYS-Login-Shell verwendet, kann
der normale Windows-Pfad zu Node.js fehlen. Der Manager verwendet fuer Start
und Build deshalb aufgeloeste absolute Pfade zu `node.exe` und `npm.cmd` und
unterstuetzt bei abweichender Installation `LIARA_NODE_EXE` sowie
`LIARA_NPM_EXE`.

Fuer die kontrollierte Erprobung der Node.js-Current-Linie verwaltet dieselbe
GUI zusaetzlich `Frontend Web UI [Node 26]` auf Port `3002`. Der Dienst startet
die portable Runtime aus `C:\ai\runtimes\node-v26.7.0-win-x64` und setzt
`NEXT_DIST_DIR=.next-node26`. `Build Test` erzeugt daher einen vollstaendig vom
Node-24-Bundle `.next` getrennten Produktionsbuild. Die Runtime kann ueber
`LIARA_NODE26_HOME`, `LIARA_NODE26_EXE` und `LIARA_NPM26_EXE` ueberschrieben
werden. Ein realer Canary gegen `/architecture` auf Port `3002` wurde mit
HTTP 200 verifiziert.

Die lokale API-CORS-Defaultliste erlaubt beide Loopback-Urspruenge auf
`localhost` und `127.0.0.1`: Port `3001` fuer Node 24 und Port `3002` fuer
Node 26. Eine explizite Liste in `LIARA_API_CORS_ALLOW_ORIGINS` ersetzt diese
Defaults weiterhin vollstaendig.
