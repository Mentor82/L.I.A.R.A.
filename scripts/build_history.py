import sqlite3
import argparse
import json
import platform
import sys
from pathlib import Path
from typing import Any

CATEGORIES = ["Idee", "Geplant", "In Arbeit", "Umgesetzt", "Fix", "Test", "Verworfen", "Erkenntnis", "Diskussion", "Entscheidung"]
ENTRY_TYPES = ["build", "idea", "chat", "decision", "fix", "test", "note"]
STATUSES = ["success", "failed", "skipped", ""]

# Resolve paths to keep the DB centralized in the project root
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent
DB_PATH = PROJECT_ROOT / ".build_history.sqlite"

def init_db():
    """Initializes the SQLite schema if it doesn't exist and migrates it."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS builds (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                version TEXT,
                component TEXT,
                os TEXT,
                status TEXT,
                duration REAL,
                worker TEXT,
                category TEXT,
                notes TEXT
            )
        """)
        
        # Migrations for old schema
        old_columns = [("worker", "TEXT", ""), ("category", "TEXT", "")]
        for col, typedef, default in old_columns:
            try:
                conn.execute(f"ALTER TABLE builds ADD COLUMN {col} {typedef}")
            except sqlite3.OperationalError:
                pass

        # Migrations for new generic schema
        new_columns = [
            ("entry_type", "TEXT NOT NULL", "'build'"),
            ("source", "TEXT NOT NULL", "''"),
            ("project", "TEXT NOT NULL", "''"),
            ("topic", "TEXT NOT NULL", "''"),
            ("title", "TEXT NOT NULL", "''"),
            ("tags", "TEXT NOT NULL", "''"),
            ("meta_json", "TEXT NOT NULL", "'{}'")
        ]
        
        for col, typedef, default in new_columns:
            try:
                conn.execute(f"ALTER TABLE builds ADD COLUMN {col} {typedef} DEFAULT {default}")
            except sqlite3.OperationalError:
                pass

        # Create recommended indexes
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_build_history_timestamp ON builds(timestamp DESC);",
            "CREATE INDEX IF NOT EXISTS idx_build_history_entry_type ON builds(entry_type);",
            "CREATE INDEX IF NOT EXISTS idx_build_history_project ON builds(project);",
            "CREATE INDEX IF NOT EXISTS idx_build_history_component ON builds(component);",
            "CREATE INDEX IF NOT EXISTS idx_build_history_topic ON builds(topic);",
            "CREATE INDEX IF NOT EXISTS idx_build_history_source ON builds(source);",
            "CREATE INDEX IF NOT EXISTS idx_build_history_category ON builds(category);",
            "CREATE INDEX IF NOT EXISTS idx_build_history_worker ON builds(worker);"
        ]
        for idx in indexes:
            conn.execute(idx)

def insert_entry(
    version: str, component: str, status: str, duration: float, worker: str, category: str, notes: str,
    entry_type: str, source: str, project: str, topic: str, title: str, tags: str, meta_json: str
):
    os_name = platform.system()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO builds (
                version, component, os, status, duration, worker, category, notes,
                entry_type, source, project, topic, title, tags, meta_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (version, component, os_name, status, duration, worker, category, notes,
              entry_type, source, project, topic, title, tags, meta_json))
    
    cat_label = f" [{category}]" if category else ""
    type_label = f" ({entry_type})" if entry_type else ""
    title_label = f" - {title}" if title else ""
    print(f"[SUCCESS] Recorded{type_label}: {component or 'general'} v{version or '-'} -> {status or 'none'}{cat_label}{title_label} (worker: {worker or '-'})")

def record_build(version: str, component: str, status: str, duration: float, worker: str, category: str, notes: str):
    """Records a new build entry (backward compatibility)."""
    insert_entry(
        version=version, component=component, status=status, duration=duration, worker=worker, category=category, notes=notes,
        entry_type="build", source="script", project="", topic="", title="", tags="", meta_json="{}"
    )

def add_entry(args):
    """Adds a generic history entry."""
    meta_json = args.meta_json
    if meta_json:
        try:
            json.loads(meta_json)
        except json.JSONDecodeError:
            print("Error: --meta-json must be a valid JSON string.")
            sys.exit(1)
            
    insert_entry(
        version=args.version, component=args.component, status=args.status, duration=args.duration,
        worker=args.worker, category=args.category, notes=args.content,
        entry_type=args.type, source=args.source, project=args.project, topic=args.topic,
        title=args.title, tags=args.tags, meta_json=meta_json
    )

def get_builds(limit: int = 50, filters_dict: dict[str, Any] | None = None, search_query: str | None = None) -> list[dict]:
    filters = []
    params = []
    
    if filters_dict:
        for k, v in filters_dict.items():
            if v:
                filters.append(f"LOWER({k}) = LOWER(?)")
                params.append(v)
                
    if search_query:
        search_term = f"%{search_query}%"
        search_conds = []
        for col in ["title", "notes", "component", "topic", "project", "worker", "tags"]:
            search_conds.append(f"{col} LIKE ?")
            params.append(search_term)
        filters.append("(" + " OR ".join(search_conds) + ")")

    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params.append(max(1, limit))
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute(f"SELECT * FROM builds {where} ORDER BY timestamp DESC LIMIT ?", params)
        return [dict(row) for row in cur.fetchall()]

def print_builds(limit: int = 50, filters_dict: dict[str, Any] | None = None, search_query: str | None = None):
    builds = get_builds(limit, filters_dict=filters_dict, search_query=search_query)
    if not builds:
        print("No entries found.")
        return

    print(f"{'ID':<5} | {'Time':<19} | {'Type':<8} | {'Cat':<12} | {'Component':<15} | {'Status':<8} | Notes")
    print("-" * 110)
    for b in builds:
        time_str = b['timestamp'][:19]
        entry_type = (b.get('entry_type') or '-')[:8]
        cat = (b.get('category') or '-')[:12]
        comp = (b.get('component') or '-')[:15]
        status = (b.get('status') or '-')[:8]
        notes_raw = b.get('notes') or ''
        notes_short = (notes_raw[:50] + '…') if len(notes_raw) > 50 else notes_raw
        print(f"{b['id']:<5} | {time_str:<19} | {entry_type:<8} | {cat:<12} | {comp:<15} | {status:<8} | {notes_short}")

def _delete_entry(entry_id: int) -> None:
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("SELECT id FROM builds WHERE id = ?", (entry_id,))
        if cur.fetchone() is None:
            print(f"[ERROR] No entry with ID {entry_id} found.")
            return
        con.execute("DELETE FROM builds WHERE id = ?", (entry_id,))
        con.commit()
    print(f"[DELETED] Entry {entry_id} removed.")


# Columns that update is allowed to change
_UPDATABLE_COLUMNS = {
    "status", "notes", "category", "worker", "component",
    "version", "title", "topic", "project", "source", "tags",
}


def _update_entry(entry_id: int, fields: dict[str, str], append_notes: str | None) -> None:
    """Update one or more fields of an existing entry by ID."""
    with sqlite3.connect(DB_PATH) as con:
        cur = con.execute("SELECT * FROM builds WHERE id = ?", (entry_id,))
        row = cur.fetchone()
        if row is None:
            print(f"[ERROR] No entry with ID {entry_id} found.")
            sys.exit(1)

        sets, params = [], []
        for col, val in fields.items():
            if col not in _UPDATABLE_COLUMNS:
                print(f"[ERROR] Column '{col}' cannot be updated.")
                sys.exit(1)
            sets.append(f"{col} = ?")
            params.append(val)

        if append_notes:
            # Append to existing notes rather than replace
            sets.append("notes = notes || ?")
            params.append("\n\n" + append_notes)

        if not sets:
            print("[WARNING] Nothing to update. Provide at least one field flag.")
            return

        params.append(entry_id)
        con.execute(f"UPDATE builds SET {', '.join(sets)} WHERE id = ?", params)
        con.commit()
    print(f"[UPDATED] Entry {entry_id} updated: {', '.join(fields.keys())}{' +notes' if append_notes else ''}.")


def export_json(limit: int = 1000):
    builds = get_builds(limit=limit)
    for b in builds:
        mj = b.get("meta_json")
        if mj:
            try:
                b["meta_json"] = json.loads(mj)
            except:
                pass # keep as string
    print(json.dumps(builds, indent=2))

def launch_tui():
    try:
        from textual.app import App, ComposeResult
        from textual.widgets import Header, Footer, DataTable, Button, Label, Static, Input, TextArea
        from textual.screen import ModalScreen
        from textual.containers import Vertical, Horizontal, ScrollableContainer
    except ImportError:
        print("Error: 'textual' is not installed. Please install it with 'pip install textual'.")
        sys.exit(1)

    class DetailModal(ModalScreen):
        """Floating detail window for a single history entry."""

        DEFAULT_CSS = """
        DetailModal {
            align: center middle;
        }
        DetailModal > Vertical {
            width: 80%;
            max-height: 80%;
            background: $surface;
            border: solid $primary;
            padding: 0 1;
        }
        DetailModal #modal-title-bar {
            height: 3;
            align: left middle;
            background: $primary;
            padding: 0 1;
        }
        DetailModal #modal-title {
            width: 1fr;
            text-style: bold;
            color: $text;
        }
        DetailModal #modal-close {
            width: 5;
            min-width: 5;
            background: $error;
            color: $text;
            border: none;
        }
        DetailModal #modal-body {
            padding: 1;
            overflow-y: auto;
            height: 1fr;
        }
        DetailModal .field-label {
            text-style: bold;
            color: $accent;
        }
        DetailModal .field-value {
            margin-bottom: 1;
        }
        DetailModal #notes-area {
            height: auto;
            max-height: 40%;
            border: solid $primary-darken-2;
            background: $surface-darken-1;
            padding: 0 1;
            overflow-y: auto;
            margin-bottom: 1;
        }
        """

        def __init__(self, entry: dict):
            super().__init__()
            self._entry = entry

        def compose(self) -> ComposeResult:
            e = self._entry
            comp = e.get("component") or "-"
            title = e.get("title") or comp
            with Vertical():
                with Horizontal(id="modal-title-bar"):
                    yield Label(f"  #{e['id']} — {title}", id="modal-title")
                    yield Button("✕", id="modal-close", variant="error")
                with Vertical(id="modal-body"):
                    meta_json = e.get("meta_json") or "{}"
                    if isinstance(meta_json, (dict, list)):
                        meta_json = json.dumps(meta_json, indent=2, ensure_ascii=False)
                    fields = [
                        ("ID",         str(e.get("id", "-"))),
                        ("Timestamp",  e.get("timestamp", "-")),
                        ("Type",       e.get("entry_type") or "-"),
                        ("Category",   e.get("category") or "-"),
                        ("Status",     e.get("status") or "-"),
                        ("Component",  e.get("component") or "-"),
                        ("Project",    e.get("project") or "-"),
                        ("Topic",      e.get("topic") or "-"),
                        ("Source",     e.get("source") or "-"),
                        ("Worker",     e.get("worker") or "-"),
                        ("Version",    e.get("version") or "-"),
                        ("Duration",   str(e.get("duration") or "0")),
                        ("Tags",       e.get("tags") or "-"),
                        ("Title",      e.get("title") or "-"),
                        ("Notes",      e.get("notes") or "—"),
                        ("Meta JSON",  meta_json or "{}"),
                    ]
                    for label, value in fields:
                        yield Static(label, classes="field-label")
                        if label in {"Notes", "Meta JSON"}:
                            yield TextArea(value, id="notes-area", read_only=True)
                        else:
                            yield Static(value, classes="field-value")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "modal-close":
                self.dismiss()

        def on_key(self, event) -> None:
            if event.key == "escape":
                self.dismiss()

    class FormModal(ModalScreen):
        """Shared create / edit form for history entries."""

        DEFAULT_CSS = """
        FormModal {
            align: center middle;
        }
        FormModal > Vertical {
            width: 90%;
            max-height: 90%;
            background: $surface;
            border: solid $primary;
            padding: 0 1;
        }
        FormModal #form-title-bar {
            height: 3;
            align: left middle;
            background: $primary;
            padding: 0 1;
        }
        FormModal #form-title {
            width: 1fr;
            text-style: bold;
            color: $text;
        }
        FormModal #form-body {
            padding: 1;
            height: 1fr;
        }
        FormModal .field-label {
            text-style: bold;
            color: $accent;
            margin-top: 1;
        }
        FormModal .hint {
            color: $text-muted;
            text-style: italic;
        }
        FormModal #form-buttons {
            height: 3;
            align: right middle;
            padding: 0 1;
        }
        FormModal #form-save {
            margin-right: 1;
        }
        FormModal .notes-textarea {
            height: 30%;
            border: solid $primary-darken-2;
            margin-bottom: 1;
        }
        """

        def __init__(self, entry: dict | None = None, modal_title: str = "Eintrag"):
            super().__init__()
            self._entry = entry or {}
            self._modal_title = modal_title

        def compose(self) -> ComposeResult:
            e = self._entry
            with Vertical():
                with Horizontal(id="form-title-bar"):
                    yield Label(f"  {self._modal_title}", id="form-title")
                    yield Button("✕", id="form-cancel-x", variant="error")
                with ScrollableContainer(id="form-body"):
                    yield Static("Titel", classes="field-label")
                    yield Input(value=e.get("title", ""), placeholder="Kurzer Titel", id="f-title")
                    yield Static("Type", classes="field-label")
                    yield Static(f"  ({', '.join(ENTRY_TYPES)})", classes="hint")
                    yield Input(value=e.get("entry_type", ""), placeholder="idea / build / decision / ...", id="f-type")
                    yield Static("Category", classes="field-label")
                    yield Static(f"  ({', '.join(CATEGORIES)})", classes="hint")
                    yield Input(value=e.get("category", ""), placeholder="Idee / Geplant / Umgesetzt / ...", id="f-category")
                    yield Static("Status", classes="field-label")
                    yield Static("  (success, failed, skipped)", classes="hint")
                    yield Input(value=e.get("status", ""), placeholder="success / failed / skipped", id="f-status")
                    yield Static("Component", classes="field-label")
                    yield Input(value=e.get("component", ""), placeholder="z.B. planner, api, tools", id="f-component")
                    yield Static("Project", classes="field-label")
                    yield Input(value=e.get("project", ""), placeholder="z.B. liara", id="f-project")
                    yield Static("Topic", classes="field-label")
                    yield Input(value=e.get("topic", ""), placeholder="z.B. routing, memory", id="f-topic")
                    yield Static("Worker", classes="field-label")
                    yield Input(value=e.get("worker", ""), placeholder="human / GitHub Copilot / ci", id="f-worker")
                    yield Static("Version", classes="field-label")
                    yield Input(value=e.get("version", ""), placeholder="z.B. 0.1.1", id="f-version")
                    yield Static("Notes", classes="field-label")
                    yield TextArea(e.get("notes", ""), id="f-notes", classes="notes-textarea")
                with Horizontal(id="form-buttons"):
                    yield Button("Speichern", id="form-save", variant="success")
                    yield Button("Abbrechen", id="form-cancel", variant="default")

        def _collect(self) -> dict:
            return {
                "title":      self.query_one("#f-title",    Input).value.strip(),
                "entry_type": self.query_one("#f-type",     Input).value.strip(),
                "category":   self.query_one("#f-category", Input).value.strip(),
                "status":     self.query_one("#f-status",   Input).value.strip(),
                "component":  self.query_one("#f-component",Input).value.strip(),
                "project":    self.query_one("#f-project",  Input).value.strip(),
                "topic":      self.query_one("#f-topic",    Input).value.strip(),
                "worker":     self.query_one("#f-worker",   Input).value.strip(),
                "version":    self.query_one("#f-version",  Input).value.strip(),
                "notes":      self.query_one("#f-notes",    TextArea).text.strip(),
            }

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id in ("form-cancel", "form-cancel-x"):
                self.dismiss(None)
            elif event.button.id == "form-save":
                self.dismiss(self._collect())

        def on_key(self, event) -> None:
            if event.key == "escape":
                self.dismiss(None)

    class ConfirmModal(ModalScreen):
        """Simple OK / Abbrechen confirmation dialog."""

        DEFAULT_CSS = """
        ConfirmModal {
            align: center middle;
        }
        ConfirmModal > Vertical {
            width: 60%;
            background: $surface;
            border: solid $warning;
            padding: 1 2;
        }
        ConfirmModal #confirm-msg {
            text-align: center;
            margin-bottom: 2;
        }
        ConfirmModal #confirm-buttons {
            height: 3;
            align: center middle;
        }
        ConfirmModal #confirm-ok {
            margin-right: 2;
        }
        """

        def __init__(self, message: str):
            super().__init__()
            self._message = message

        def compose(self) -> ComposeResult:
            with Vertical():
                yield Static(self._message, id="confirm-msg")
                with Horizontal(id="confirm-buttons"):
                    yield Button("OK", id="confirm-ok", variant="error")
                    yield Button("Abbrechen", id="confirm-cancel", variant="default")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss(event.button.id == "confirm-ok")

        def on_key(self, event) -> None:
            if event.key == "escape":
                self.dismiss(False)

    class BuildHistoryApp(App):
        TITLE = "LIARA Build History"
        BINDINGS = [
            ("q", "quit",    "Quit"),
            ("r", "refresh", "Refresh"),
            ("v", "view",    "View"),
            ("a", "toggle_auto_refresh", "Auto-Refresh"),
            ("n", "new",     "Neu"),
            ("e", "edit",    "Bearbeiten"),
            ("d", "delete",  "Löschen"),
        ]

        def __init__(self):
            super().__init__()
            self._entries: list[dict] = []
            self._auto_refresh_enabled = True
            self._auto_refresh_seconds = 10.0
            self._auto_refresh_timer = None

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal():
                yield Button("View (V)", id="view-entry-btn")
                yield Button("Auto-Refresh: ON", id="toggle-auto-refresh-btn")
                yield Static("Klick oder V öffnet Details", id="audit-help")
            yield DataTable()
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one(DataTable)
            table.cursor_type = "row"
            table.zebra_stripes = True
            table.add_columns("ID", "Timestamp", "Type", "Category", "Project", "Component", "Worker", "Title", "Status", "Notes")
            self._auto_refresh_timer = self.set_interval(self._auto_refresh_seconds, self._auto_refresh_tick)
            self._sync_auto_refresh_button()
            self.load_data()

        def action_refresh(self) -> None:
            self.load_data()

        def action_view(self) -> None:
            entry = self._current_entry()
            if entry is not None:
                self.push_screen(DetailModal(entry))

        def action_toggle_auto_refresh(self) -> None:
            self._auto_refresh_enabled = not self._auto_refresh_enabled
            self._sync_auto_refresh_button()
            state = "aktiviert" if self._auto_refresh_enabled else "deaktiviert"
            self.notify(f"Auto-Refresh {state}")

        def _auto_refresh_tick(self) -> None:
            if not self._auto_refresh_enabled:
                return
            if len(self.screen_stack) > 1:
                return
            self.load_data()

        def _sync_auto_refresh_button(self) -> None:
            button = self.query_one("#toggle-auto-refresh-btn", Button)
            button.label = f"Auto-Refresh: {'ON' if self._auto_refresh_enabled else 'OFF'}"

        def load_data(self) -> None:
            table = self.query_one(DataTable)
            current_row = table.cursor_row if table.row_count else 0
            table.clear()
            self._entries = get_builds()
            for b in self._entries:
                notes_raw = b.get("notes") or ""
                notes_short = (notes_raw[:80] + "…") if len(notes_raw) > 80 else notes_raw
                table.add_row(
                    str(b["id"]),
                    b["timestamp"],
                    b.get("entry_type") or "-",
                    b.get("category") or "-",
                    b.get("project") or "-",
                    b.get("component") or "-",
                    b.get("worker") or "-",
                    b.get("title") or "-",
                    b.get("status") or "-",
                    notes_short,
                )
            if self._entries:
                table.move_cursor(row=min(max(current_row, 0), len(self._entries) - 1), column=0)

        def on_data_table_row_selected(self, event) -> None:
            self.action_view()

        def on_data_table_cell_selected(self, event) -> None:
            idx = event.coordinate.row
            if 0 <= idx < len(self._entries):
                table = self.query_one(DataTable)
                table.move_cursor(row=idx, column=event.coordinate.column)
                self.action_view()

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "view-entry-btn":
                self.action_view()
            elif event.button.id == "toggle-auto-refresh-btn":
                self.action_toggle_auto_refresh()

        def _current_entry(self) -> dict | None:
            table = self.query_one(DataTable)
            idx = table.cursor_row
            if 0 <= idx < len(self._entries):
                return self._entries[idx]
            return None

        def action_new(self) -> None:
            def _on_result(data: dict | None) -> None:
                if data is None:
                    return
                insert_entry(
                    version=data.get("version", ""),
                    component=data.get("component", ""),
                    status=data.get("status", "skipped"),
                    duration=0,
                    worker=data.get("worker", "human"),
                    category=data.get("category", ""),
                    notes=data.get("notes", ""),
                    entry_type=data.get("entry_type", "note"),
                    source="tui",
                    project=data.get("project", ""),
                    topic=data.get("topic", ""),
                    title=data.get("title", ""),
                    tags="",
                    meta_json="{}",
                )
                self.load_data()
            self.push_screen(FormModal(modal_title="Neuer Eintrag"), callback=_on_result)

        def action_edit(self) -> None:
            entry = self._current_entry()
            if entry is None:
                return
            def _on_result(data: dict | None) -> None:
                if data is None:
                    return
                updatable = {k: v for k, v in data.items() if k in _UPDATABLE_COLUMNS and v != ""}
                if updatable:
                    _update_entry(entry["id"], updatable, None)
                self.load_data()
            self.push_screen(FormModal(entry=entry, modal_title=f"Bearbeiten #{entry['id']}"), callback=_on_result)

        def action_delete(self) -> None:
            entry = self._current_entry()
            if entry is None:
                return
            worker = (entry.get("worker") or "").lower()
            etype  = (entry.get("entry_type") or "").lower()
            if worker != "human" and etype != "idea":
                self.notify("Löschen nur für Einträge von 'human' oder Typ 'idea' erlaubt.", severity="warning")
                return
            title_hint = entry.get("title") or entry.get("component") or str(entry["id"])
            def _on_confirm(ok: bool | None) -> None:
                if ok:
                    _delete_entry(entry["id"])
                    self.load_data()
            self.push_screen(
                ConfirmModal(f"Eintrag #{entry['id']} — {title_hint}\n\nWirklich löschen?"),
                callback=_on_confirm,
            )

    app = BuildHistoryApp()
    app.run()

def main():
    init_db()
    parser = argparse.ArgumentParser(description="LIARA Build History Tracker (CLI + TUI + AI JSON)")
    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Record Command (backward compatible)
    record_parser = subparsers.add_parser("record", help="Record a new build execution")
    record_parser.add_argument("--version", required=True, help="Version string (e.g., 0.1.1)")
    record_parser.add_argument("--component", required=True, help="Component name (e.g., api, orchestrator, ui)")
    record_parser.add_argument("--status", required=True, choices=["success", "failed", "skipped"], help="Build or run status")
    record_parser.add_argument("--duration", type=float, default=0.0, help="Build duration in seconds")
    record_parser.add_argument("--worker", default="", help="Who performed the work (e.g., Copilot, human, ci)")
    record_parser.add_argument("--category", default="", choices=CATEGORIES + [""], metavar="CATEGORY")
    record_parser.add_argument("--notes", default="", help="Additional notes, error messages, or commit hashes")

    # Add Command (generic history)
    add_parser = subparsers.add_parser("add", help="Add a generic history entry")
    add_parser.add_argument("--type", default="note", choices=ENTRY_TYPES, help="Entry type")
    add_parser.add_argument("--category", default="", help="Category (e.g. Idee, Entscheidung)")
    add_parser.add_argument("--project", default="", help="Project name")
    add_parser.add_argument("--component", default="", help="Component name")
    add_parser.add_argument("--topic", default="", help="Topic")
    add_parser.add_argument("--source", default="", help="Source (e.g., human, chatgpt)")
    add_parser.add_argument("--worker", default="", help="Worker name")
    add_parser.add_argument("--status", default="", choices=STATUSES, help="Status")
    add_parser.add_argument("--version", default="", help="Version")
    add_parser.add_argument("--duration", type=float, default=0.0, help="Duration in seconds")
    add_parser.add_argument("--title", default="", help="Short descriptive title")
    add_parser.add_argument("--content", default="", help="Content/Notes")
    add_parser.add_argument("--tags", default="", help="Comma separated tags")
    add_parser.add_argument("--meta-json", default="{}", help="Valid JSON string with metadata")

    # List Command
    list_parser = subparsers.add_parser("list", help="List recent entries in CLI")
    list_parser.add_argument("--limit", type=int, default=50, help="Number of records to show")
    list_parser.add_argument("--type", default=None, help="Filter by type")
    list_parser.add_argument("--category", default=None, help="Filter by category")
    list_parser.add_argument("--project", default=None, help="Filter by project")
    list_parser.add_argument("--component", default=None, help="Filter by component name")
    list_parser.add_argument("--topic", default=None, help="Filter by topic")
    list_parser.add_argument("--source", default=None, help="Filter by source")
    list_parser.add_argument("--worker", default=None, help="Filter by worker")
    list_parser.add_argument("--status", default=None, help="Filter by status")
    list_parser.add_argument("--search", default=None, help="Simple search text")

    # Search Command
    search_parser = subparsers.add_parser("search", help="Search history entries")
    search_parser.add_argument("query", help="Search query")
    search_parser.add_argument("--limit", type=int, default=50, help="Number of records to show")

    # Export Command
    subparsers.add_parser("export", help="Export build history as JSON (for AI consumption)")

    # Update Command
    update_parser = subparsers.add_parser("update", help="Update fields of an existing entry by ID")
    update_parser.add_argument("id", type=int, help="ID of the entry to update")
    update_parser.add_argument("--status", default=None, choices=["success", "failed", "skipped", ""], help="New status")
    update_parser.add_argument("--notes", default=None, help="Replace notes")
    update_parser.add_argument("--append-notes", default=None, metavar="TEXT", help="Append text to existing notes")
    update_parser.add_argument("--category", default=None, help="New category")
    update_parser.add_argument("--worker", default=None, help="New worker")
    update_parser.add_argument("--component", default=None, help="New component")
    update_parser.add_argument("--version", default=None, help="New version")
    update_parser.add_argument("--title", default=None, help="New title")
    update_parser.add_argument("--topic", default=None, help="New topic")
    update_parser.add_argument("--project", default=None, help="New project")
    update_parser.add_argument("--source", default=None, help="New source")
    update_parser.add_argument("--tags", default=None, help="New tags")

    # Delete Command
    delete_parser = subparsers.add_parser("delete", help="Delete an entry by ID")
    delete_parser.add_argument("id", type=int, help="ID of the entry to delete")

    # TUI Command
    subparsers.add_parser("tui", help="Launch the interactive Textual UI")

    args = parser.parse_args()

    if args.command == "record":
        record_build(args.version, args.component, args.status, args.duration, args.worker, args.category, args.notes)
    elif args.command == "add":
        if not args.title and not args.content:
            print("Warning: It is recommended to provide at least --title or --content.")
        add_entry(args)
    elif args.command == "list":
        filters_dict = {
            "entry_type": args.type,
            "category": args.category,
            "project": args.project,
            "component": args.component,
            "topic": args.topic,
            "source": args.source,
            "worker": args.worker,
            "status": args.status
        }
        print_builds(args.limit, filters_dict=filters_dict, search_query=args.search)
    elif args.command == "search":
        print_builds(args.limit, search_query=args.query)
    elif args.command == "update":
        fields = {k: v for k, v in vars(args).items()
                  if k not in ("command", "id", "append_notes") and v is not None}
        _update_entry(args.id, fields, args.append_notes)
    elif args.command == "delete":
        _delete_entry(args.id)
    elif args.command == "export":
        export_json()
    elif args.command == "tui":
        launch_tui()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()