# TUI Architecture Matrix

## Framework-Auswahl nach Anforderung

### Rich (statische Admin-Views)

Für schnelle, lesbare CLI-Ausgaben ohne Full-TUI: Tabellen, Panels, Live-Refresh.

#### Komponenten

- **sys-audit-view** — `/sys` Request-Historie, geblockte Calls, Top-Commands, Top-Sources und Suspicious Events

#### Eigenschaften

- Kein Widget-Framework nötig
- Sofort nutzbar per `python -m`
- `--follow` für Live-Refresh (Ctrl+C)
- Kein Mouse-Support

### Textual (Rich-based TUI Framework)

Für komplexe, mehrpanel-basierte Interfaces mit Widgets und Live-Updates.

#### Komponenten (Textual)

- **Admin-Console** — Komplexe Menüs, Status-Dashboards, Konfiguration
- **Memory-Inspector** — Tree-Views für Speicherhierarchie, Live-Updates
- **Tool-Registry-Viewer** — Tabellarische Listen mit Drill-down, Such-Funktionalität
- **Worker-Monitor** — Real-time Metriken, Fortschrittsbalken, Service-Status
- **Graph-Ansichten (Neo4j)** — Visuelle Graphen, Knoten-Drill-down
- **Live-Logs** — Scrollbare Log-Streams mit Filtering und Highlighting

#### Eigenschaften (Textual)

- Full TUI mit mehreren Panels
- Mouse-Support
- Reactive updates
- CSS-Styling möglich

### Prompt Toolkit (Input-fokussiert)

Für CLI-basierte REPLs mit guter History, Completion und Syntax-Highlighting.

#### Komponenten (Prompt Toolkit)

- **Liara-Shell** — Upgrade der aktuellen REPL mit Prompt Toolkit
- **Agent-REPL** — Für Agent-Debugging und Inspection
- **Debug-Prompts** — Ad-hoc Input mit Context-Highlighting

#### Eigenschaften (Prompt Toolkit)

- Fokus auf Input/Output
- Auto-Completion
- Command History
- Syntax-Highlighting
- Lightweight

## Implementierungs-Roadmap

### Phase 1: Liara-Shell (Prompt Toolkit)

Modularisiert die REPL unter `services/tui/liara_shell_modules/` mit kompatiblem Entrypoint `services/tui/liara_shell.py`:

- Besseres Syntax-Highlighting für Commands
- Auto-Completion für `/` Befehle
- Persistente History (`~/.liara/history`)
- Multi-line Input (behalte `"""` Trigger)
- Live-Statusleiste (API Health, Session Info)

### Phase 2: Worker-Monitor (Textual)

Dashboard für Service-Überwachung:

- Service-Status (Running/Stopped/Error)
- CPU/Memory Charts
- Live Log-Stream
- Quick-Actions (Start/Stop/Restart)

### Phase 3: Memory-Inspector (Textual)

Debug-Tool für Memory-System:

- Tree-View der Memory-Struktur
- Search/Filter
- Live Context-Updates

### Phase 4: Admin-Console (Textual)

Zentrale Verwaltungskonsole:

- Settings-Editor
- Service-Konfiguration
- User-Management
- Audit-Logs

### Phase 5: Graph-Viewer (Textual)

Neo4j-Graph-Visualisierung in Terminal.

## Installation & Abhängigkeiten

```bash
pip install textual prompt-toolkit rich
```

## Verwendung

```bash
# sys-audit-view (Textual)
python -m services.tui.sys_audit_tui
python -m services.tui.sys_audit_tui --blocked-only
python -m services.tui.sys_audit_tui --follow --interval 3
python -m services.tui.sys_audit_tui --limit 50
python -m services.tui.sys_audit_tui --source orchestrator --risk-level high --command-family network

# Liara-Shell (Prompt Toolkit)
python -m services.tui.liara_shell

# Worker-Monitor (Textual)
python -m services.tui.worker_monitor

# Memory-Inspector (Textual)
python -m services.tui.memory_inspector

# Admin-Console (Textual)
python -m services.tui.admin_console

# Graph-Viewer (Textual)
python -m services.tui.graph_viewer

# Unified Launcher (Textual)
python -m services.tui.launcher
```

Im Textual-Mode unterstuetzt die Audit-Ansicht jetzt ein Detail-Overlay fuer Eintraege:

- `v` oeffnet den aktuell markierten Recent-Entry
- Klick/Select auf einen Recent-Entry oeffnet ebenfalls die Detailansicht
- `a` schaltet Auto-Refresh ein oder aus
- Die Monitoring-Zeile zeigt Refresh-Zeit, sichtbare Treffer, Suspicious-Signale und den letzten Fehlerzustand

### TUI Consistency Update (2026-04-20)

- Worker-Monitor: `a` toggles auto-refresh; summary now shows API, refresh mode, last refresh and last error.
- Memory-Inspector: `a` toggles auto-refresh in addition to `m` for tool-messages; metadata bar now includes last refresh/error.
- Graph-Viewer: `a` toggles schema auto-refresh; connection line now shows refresh mode directly.
- Admin-Console: `v` opens a quick row-view event for selected backend/tool entries.
- Liara-Shell: `/clear` and `/cls` clear terminal output.
- Launcher: now includes `Sys Audit TUI` as option `6`.

### sys-audit-view Filter Controls

#### CLI-Filter

- `--source <name>`
- `--risk-level <all|low|medium|high>`
- `--command-family <all|network|python|filesystem|inspection|other>`

#### Textual Keybindings

- `b` toggles blocked-only
- `s` cycles source filter
- `k` cycles risk filter
- `f` cycles command-family filter

### Audit Source Of Truth

- Primary code citations for audit findings must reference source paths under `services/**`, `workers/**`, `shared/**`, `tests/**`, `scripts/**`, `docs/**`.
- Generated paths such as `build/**`, `build/lib/**`, `frontend/**/dist/**`, and `liara.egg-info/**` are non-authoritative for code findings.
- Use generated paths only as secondary evidence for packaging/runtime behavior.

See `docs/AUDIT_SOURCE_OF_TRUTH.md` for the full policy.

## Verzeichnisstruktur

```text
services/
  tui/
    __init__.py
    launcher.py             # Unified TUI Launcher
    sys_audit_tui.py        # Textual Admin-View: /sys Audit-Log
    liara_shell.py          # Kompatibler Entrypoint
    worker_monitor.py       # Kompatibler Entrypoint
    memory_inspector.py     # Kompatibler Entrypoint
    admin_console.py        # Kompatibler Entrypoint
    apps/
      __init__.py
      liara_shell.py        # App-Entrypoint Wrapper
      worker_monitor.py     # App-Entrypoint Wrapper
      memory_inspector.py   # App-Entrypoint Wrapper
      admin_console.py      # App-Entrypoint Wrapper
    liara_shell_modules/
      __init__.py
      app.py                # run_shell/main
      commands.py           # Command-Router + Handler
      prompting.py          # Prompt-Session + Multiline-Input
      state.py              # ShellState Dataclass
      ui.py                 # Rendering/Help/Status Output
      constants.py          # Completion, Limits, Style
    graph_viewer.py         # Textual Graph-Renderer
    shared/
      __init__.py
      textual.py            # Lazy Textual Loader
      formatting.py         # Gemeinsame Style-Formatter
```
