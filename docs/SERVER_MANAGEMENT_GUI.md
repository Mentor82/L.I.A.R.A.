# LIARA Server Manager

## Zweck

Der Server Manager ist eine native Desktop-Anwendung (C + GTK4), um lokale LIARA-Services stabil zu starten, zu stoppen, zu ueberwachen und zu debuggen.

Abgedeckter Funktionsumfang:

- Start / Stop / Restart einzelner Services
- Start/Stop/Restart fuer alle Services
- Health-Checks mit Debounce gegen kurzzeitige Fehler
- Live-Log-Streaming in der GUI
- Persistente PID-Caches pro Service
- Recovery von laufenden Services beim Neustart des Managers
- separater Next.js-Produktionsbuild fuer das Webfrontend, ohne automatischen
  Service-Restart
- paralleler Node-26-Testpfad mit eigenem Build-Verzeichnis und Port

## Komponenten

- GUI-Quelle: `frontend/server-manager/src/server_manager.c`
- Rust-Launcher: `frontend/server-manager/launcher-rust/src/main.rs`
- Rust-Metadaten/Buildprofil: `frontend/server-manager/launcher-rust/Cargo.toml`
- Build-Konfiguration: `frontend/server-manager/meson.build`
- Packaging-Skript: `frontend/server-manager/package.ps1`
- Primere Doku (Build/Package kurz): `frontend/server-manager/README.md`
- aktuell lokal verwendete Python GUI: `server_management_gui.py`

## Webfrontend-Build

Die Python-GUI zeigt in der Zeile `Frontend Web UI` neben Start, Stop und
Restart einen separaten Button `Build`. Er fuehrt im Hintergrund im Verzeichnis
`frontend/web-ui` aus:

```text
npm run build
```

Die Build-Ausgabe wird mit dem Praefix `[frontend:build]` in das Live-Log
geschrieben. Solange der Prozess laeuft, ist der Button deaktiviert und zeigt
`Building...`. Ein erfolgreicher Build startet oder stoppt den Frontend-Dienst
nicht. Die Aktivierung bleibt als bewusste zweite Operation beim vorhandenen
`Restart`-Button. Dadurch kann ein Buildfehler den laufenden Produktionsbundle
nicht automatisch ausser Betrieb nehmen.

Daneben fuehrt die GUI `Frontend Web UI [Node 26]` als getrennten Testdienst:

| Pfad | Runtime | Port | Build-Verzeichnis | Aktion |
| --- | --- | ---: | --- | --- |
| Standard | Node.js 24 LTS | `3001` | `.next` | `Build` |
| Current-Test | portable Node.js 26 | `3002` | `.next-node26` | `Build Test` |

Der Testpfad verwendet die verifizierte portable Runtime unter
`C:\ai\runtimes\node-v26.7.0-win-x64`. Sie wird nicht in den globalen `PATH`
eingetragen. Start und Build erhalten stattdessen einen pro Prozess gesetzten
`PATH` sowie `NEXT_DIST_DIR=.next-node26`. Dadurch koennen beide Frontends
parallel laufen und ihre Build-Artefakte nicht gegenseitig ueberschreiben.

Der MSYS-Starter liefert nicht zwingend den normalen Windows-Node-PATH. Die
Python-GUI loest `node.exe` und `npm.cmd` deshalb vor dem Spawn auf absolute
Windows-Pfade auf. Die Reihenfolge ist:

1. `LIARA_NODE_EXE` beziehungsweise `LIARA_NPM_EXE`;
2. Treffer aus dem aktuellen `PATH`;
3. `C:\Program Files\nodejs\node.exe` beziehungsweise `npm.cmd`.

Fuer den Node-26-Testpfad gelten getrennt:

1. `LIARA_NODE26_EXE` beziehungsweise `LIARA_NPM26_EXE`;
2. Runtime-Verzeichnis aus `LIARA_NODE26_HOME`;
3. `C:\ai\runtimes\node-v26.7.0-win-x64`.

Kann ein nicht durch den Service Guard verwalteter Prozess trotzdem nicht
gestartet werden, wird der Fehler im Live-Log dem betroffenen Service
zugeordnet. `Start All` verliert dadurch nicht mehr ungefangen seinen
Hintergrundthread.

## Verwaltete Services (Standard)

- LIARA API
	- Startkommando: `python -m uvicorn services.api.app:app --host 0.0.0.0 --port 8010`
	- Health: `http://127.0.0.1:8010/health`
	- Match-Token: `services.api.app`
- LIARA Memory
	- Startkommando: `python -m uvicorn services.memory.app:app --host <...> --port <...>`
	- Health: `http://127.0.0.1:8020/health`
	- Match-Token: `services.memory.app:app`
- LIARA Embedding
	- Startkommando: `python -m uvicorn services.embedding.app:app --host <...> --port <...>`
	- Health: `http://127.0.0.1:8030/health`
	- Match-Token: `services.embedding.app:app`

## Build und Start (Dev)

### Variante A: PowerShell + MSYS Bash (empfohlen)

```powershell
C:\msys64\usr\bin\bash.exe -lc 'export PATH=/ucrt64/bin:$PATH; cd /c/ai/LIARA/frontend/server-manager && meson setup builddir --wipe && meson compile -C builddir'
```

Start:

```powershell
frontend/server-manager/builddir/liara-server-manager.exe
```

### Variante B: Bereits konfiguriertes Build-Verzeichnis

```powershell
C:\msys64\usr\bin\bash.exe -lc 'export PATH=/ucrt64/bin:$PATH; cd /c/ai/LIARA/frontend/server-manager && meson compile -C builddir'
```

## Packaging (Distribution)

```powershell
cd frontend/server-manager
.\package.ps1
```

Wichtige Artefakte in `frontend/server-manager/dist`:

- `bin/liara-server-manager.exe`
- `bin/gspawn-win64-helper.exe`
- `bin/gspawn-win64-helper-console.exe`
- `lib/*.dll`
- `config/server-manager.json`
- `cache/`
- `logs/ui/server-manager.log`
- `run-liara-server-manager.exe` (Rust-Launcher, falls verfuegbar)
- `run-liara-server-manager.cmd` (Fallback)

Version pruefen:

```powershell
frontend/server-manager/dist/run-liara-server-manager.exe --version
```

## Rust-Launcher

Der bevorzugte Einstiegspunkt in der Distribution ist der Rust-Launcher:

- `frontend/server-manager/dist/run-liara-server-manager.exe`

Aufgaben des Launchers:

- setzt/normalisiert Laufzeitumgebung fuer den C/GTK-Manager
- startet den eigentlichen GUI-Binary-Einstiegspunkt in `dist/bin`
- schreibt einen Start-Stamp ins File-Log
- unterstuetzt `--version` fuer schnelle Diagnose

Quelle:

- `frontend/server-manager/launcher-rust/src/main.rs`

Build/Packaging-Verhalten:

- Wenn Rust/Cargo verfuegbar ist, baut `package.ps1` den Launcher und legt die EXE in `dist` ab.
- Wenn Cargo nicht verfuegbar ist, bleibt der CMD-Starter als Fallback erhalten.

Empfehlung fuer Betrieb:

1. Primar `run-liara-server-manager.exe` verwenden.
2. Bei fehlender EXE auf `run-liara-server-manager.cmd` ausweichen.

### Startfluss

```
run-liara-server-manager.exe   (Rust-Launcher, dist/)
   │
   ├─ setzt Umgebungsvariablen (PATH, LIARA_PROJECT_ROOT, ...)
   ├─ erstellt dist/cache/ und dist/logs/ui/ falls fehlend
   ├─ schreibt Startmarker in server-manager.log
   │
   └─► dist/bin/liara-server-manager.exe   (C/GTK4-GUI)
          │
          ├─ laedt config/server-manager.json
          ├─ startet Health-Poll (alle 3s) und Log-Streaming
          │
		  ├─► python -m uvicorn services.api.app:app --host 0.0.0.0 --port 8010   (Port 8010)
          ├─► uvicorn services.memory.app:app      (Port 8020)
          └─► uvicorn services.embedding.app:app   (Port 8030)
```

## Konfiguration

Datei:

- Dev: `config/server-manager.json`
- Dist: `frontend/server-manager/dist/config/server-manager.json`

Beispiel:

```json
{
	"autostart": false,
	"restart_on_nonzero": false,
	"start_delay_ms": 1500,
	"env_file": "C:\\ai\\LIARA\\.env",
	"log_level": "INFO"
}
```

Bedeutung:

- `autostart`
	- Startet alle Services beim GUI-Start sequentiell.
- `restart_on_nonzero`
	- `false`: Restart nach Crash (Exit != 0) wird unterdrueckt.
	- `true`: Restart wird auch nach non-zero Exit ausgefuehrt.
- `start_delay_ms`
	- Verzoegerung zwischen sequentiellen Service-Starts.
- `env_file`
	- Optionales `.env`, wird beim Spawn der Python-Prozesse eingelesen.
- `log_level`
	- `DEBUG`, `INFO`, `WARN`, `ERROR`.

## Umgebungsvariablen

Service-spezifisch:

- `LIARA_MEMORY_BIND_HOST` (Default: `0.0.0.0`)
- `LIARA_MEMORY_PORT` (Default: `8020`)
- `LIARA_EMBEDDING_BIND_HOST` (Default: `0.0.0.0`)
- `LIARA_EMBEDDING_PORT` (Default: `8030`)

Manager-intern:

- `LIARA_PROJECT_ROOT`
- `LIARA_SERVER_MANAGER_CONFIG`
- `LIARA_SERVER_MANAGER_LOG`
- `LIARA_NODE_EXE` (optionaler absoluter Pfad zu `node.exe`)
- `LIARA_NPM_EXE` (optionaler absoluter Pfad zu `npm.cmd`)

## Runtime-Verhalten

### Health-Checks

- Poll-Intervall: alle 3 Sekunden
- HTTP-Timeout: 2 Sekunden
- Debounce bei Fehlern: Status wird erst nach 2 aufeinanderfolgenden Fehlschlaegen als kritisch markiert
- JSON-Auswertung von `status` unterstuetzt:
	- direktes Feld (`{"status": "up"}`)
	- verschachteltes Feld (`{"status": {"status": "up"}}`)

Als gesund werden u. a. erkannt: `up`, `ok`, `healthy`, `ready`, `success`.

### Prozessstatus und Quellen

Der Manager kennt drei Run-Quellen:

- `owned-subprocess`: durch GUI selbst gestartet
- `cached-pid`: externer, aber erkannter laufender Prozess
- `stopped`: kein laufender Prozess

Die Quelle wird bei Wechsel ins Log geschrieben.

### PID-Cache und Recovery

Pro Service wird eine PID-Datei geschrieben (`api.pid`, `memory.pid`, `embedding.pid`) in:

- `XDG_CACHE_HOME` (falls gesetzt), sonst
- `<project_root>/cache`

Recovery-Strategien:

1. Beim Start: Laden und Validieren vorhandener PID-Dateien
2. Ohne PID-Datei: Port-basiertes Reverse-Recovery mit Prozess-Matching
3. Bei Stop/Exit: PID-Tracking und PID-Datei werden bereinigt

## Logging

- GUI-Logfenster zeigt Meldungen ab konfiguriertem `log_level`
- Persistentes File-Log unter:
	- Dev (Default): `logs/ui/server-manager.log`
	- Dist: `frontend/server-manager/dist/logs/ui/server-manager.log`

Typische Log-Marker:

- `[system] C GUI ready`
- `[<service>] started`
- `[<service>] process exited (code X)`
- `[<service>] discovered running pid ... from port ...`
- `[<service>] start skipped: port ... already in use`

## Troubleshooting

### Build-Fehler (MSYS/Toolchain)

Symptom:

- `CreateProcess failed` oder fehlende Build-Tools

Loesung:

- Build ueber MSYS Bash mit `/ucrt64/bin` im PATH ausfuehren

### Startfehler mit GLib/GSpawn Helper

Symptom:

- Fehler bezueglich `gspawn`-Helperprogramm

Loesung:

- Sicherstellen, dass in `dist/bin` enthalten sind:
	- `gspawn-win64-helper.exe`
	- `gspawn-win64-helper-console.exe`

### Port bereits belegt

Symptom:

- `start skipped: port <port> already in use`

Loesung:

- Entweder konkurrierenden Prozess stoppen oder Port-Konfiguration anpassen

### Service laeuft, aber GUI zeigt ihn nicht

Pruefen:

- passt das Kommando zum `process_match_token`?
- ist der Health-Endpunkt auf der erwarteten lokalen URL erreichbar?
- wurde `cache/*.pid` geschrieben oder wiederhergestellt?

## Architektur-Hinweise fuer Weiterentwicklung

- Health-Probes laufen asynchron und sollten nicht wieder synchron gemacht werden.
- Poll-Loop leichtgewichtig halten; schwere Recovery-Operationen nur gezielt triggern.
- Shutdown-Pfade idempotent halten (Timer/Sources immer sauber entfernen).
- Bei neuen Services immer definieren:
	- `key`
	- `health_url`
	- `process_match_token`
	- Startkommando + CWD

## Kurz-Checkliste fuer Alltag

1. Build kompilieren (`meson compile -C builddir`)
2. GUI starten
3. `Start All` ausfuehren
4. Health-Badges pruefen
5. Log auf Exit/Restart/Port-Hinweise pruefen
6. Bei Dist-Betrieb den Launcher verwenden
