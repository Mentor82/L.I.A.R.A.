from __future__ import annotations

import asyncio
import datetime
import os
import importlib
import logging
import logging.handlers
from pathlib import Path
from time import perf_counter
from typing import Any

from rich.markdown import Markdown

from .client import LiaraApiClient
from .commands import ParsedCommand, help_text, parse_command, parse_sys_invocation
from .models import ChatMode, ChatSettings
from .theme import APP_CSS


def _setup_logging(log_dir: Path | None = None, verbose: bool = False) -> logging.Logger:
    """Configure a rotating file logger + optional console handler."""
    logger = logging.getLogger("liara.tex_ui")
    logger.setLevel(logging.DEBUG)  # always capture everything at logger level
    logger.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── rotating file handler (always on) ───────────────────────────────────
    env_log_dir = os.environ.get("LIARA_TEXUI_LOG_DIR")
    workspace_log_dir = Path(__file__).resolve().parents[3] / "logs" / "tex-ui"
    base_candidates = [
        log_dir,
        Path(env_log_dir) if env_log_dir else None,
        workspace_log_dir,
        Path.cwd() / "logs" / "tex-ui",
        Path.home() / "logs",
    ]
    file_path: Path | None = None
    for candidate in base_candidates:
        if candidate is None:
            continue
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            file_path = candidate / "tex_ui.log"
            break
        except OSError:
            continue

    if file_path is not None:
        # Drop stale file handlers from previous sessions/paths so we don't split logs.
        for handler in list(logger.handlers):
            if not isinstance(handler, logging.handlers.RotatingFileHandler):
                continue
            try:
                current = Path(getattr(handler, "baseFilename", "")).resolve()
                target = file_path.resolve()
                if current != target:
                    logger.removeHandler(handler)
                    handler.close()
            except OSError:
                logger.removeHandler(handler)
                handler.close()

        has_file_handler = any(
            isinstance(h, logging.handlers.RotatingFileHandler)
            and Path(getattr(h, "baseFilename", "")).resolve() == file_path.resolve()
            for h in logger.handlers
        )
        if not has_file_handler:
            fh = logging.handlers.RotatingFileHandler(
                file_path,
                maxBytes=2 * 1024 * 1024,  # 2 MB
                backupCount=3,
                encoding="utf-8",
            )
            fh.setLevel(logging.DEBUG)
            fh.setFormatter(fmt)
            logger.addHandler(fh)
    logger._liara_log_file = str(file_path) if file_path is not None else "(disabled)"  # type: ignore[attr-defined]

    # ── optional console handler (verbose mode only) ─────────────────────────
    if verbose:
        has_console_handler = any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.handlers.RotatingFileHandler)
            for h in logger.handlers
        )
        if not has_console_handler:
            ch = logging.StreamHandler()
            ch.setLevel(logging.DEBUG)
            ch.setFormatter(fmt)
            logger.addHandler(ch)

    return logger


def _load_textual_symbols() -> tuple[Any, ...]:
    try:
        app_mod = importlib.import_module("textual.app")
        binding_mod = importlib.import_module("textual.binding")
        containers_mod = importlib.import_module("textual.containers")
        message_mod = importlib.import_module("textual.message")
        widgets_mod = importlib.import_module("textual.widgets")
    except ImportError as exc:
        raise RuntimeError(
            "textual is required for chat-ui. Install it with: pip install textual"
        ) from exc

    App = getattr(app_mod, "App")
    ComposeResult = getattr(app_mod, "ComposeResult")
    Binding = getattr(binding_mod, "Binding")
    Horizontal = getattr(containers_mod, "Horizontal")
    Vertical = getattr(containers_mod, "Vertical")
    Message = getattr(message_mod, "Message")
    Button = getattr(widgets_mod, "Button")
    DirectoryTree = getattr(widgets_mod, "DirectoryTree")
    Footer = getattr(widgets_mod, "Footer")
    Header = getattr(widgets_mod, "Header")
    RichLog = getattr(widgets_mod, "RichLog")
    Static = getattr(widgets_mod, "Static")
    TextArea = getattr(widgets_mod, "TextArea")
    DataTable = getattr(widgets_mod, "DataTable")

    return App, ComposeResult, Binding, Horizontal, Vertical, Message, Button, DirectoryTree, Footer, Header, RichLog, Static, TextArea, DataTable


def _render_status(settings: ChatSettings) -> str:
    return (
        "[bold]Session[/bold]\n"
        f"id: [cyan]{settings.session_id}[/cyan]\n"
        f"user: [cyan]{settings.user_id}[/cyan]\n"
        f"mode: [yellow]{settings.mode}[/yellow]  max: [magenta]{settings.max_tokens}[/magenta]\n"
        f"url: [dim]{settings.base_url}[/dim]\n"
        f"workspace: [dim]{settings.workspace_root}[/dim]"
    )


def _render_runtime(metrics: dict[str, Any]) -> str:
    latency = metrics.get("last_latency_ms")
    latency_text = "-" if latency in (None, 0, 0.0) else f"{float(latency):.0f} ms"
    return (
        "[bold]Runtime[/bold]\n"
        f"turns: [cyan]{metrics.get('user_turns', 0)}[/cyan] / [cyan]{metrics.get('assistant_turns', 0)}[/cyan]\n"
        f"latency: [green]{latency_text}[/green]\n"
        f"provider: [cyan]{metrics.get('provider', '-')}[/cyan]\n"
        f"model: [cyan]{metrics.get('model', '-')}[/cyan]\n"
        f"tools: [dim]{metrics.get('tools', '-')}[/dim]"
    )


def _render_cache(summary: dict[str, Any], restored_entries: int) -> str:
    return (
        "[bold]Cache[/bold]\n"
        f"api entries: [cyan]{summary.get('api_entries', 0)}[/cyan]\n"
        f"sessions: [cyan]{summary.get('transcript_sessions', 0)}[/cyan]\n"
        f"hits/misses: [green]{summary.get('hits', 0)}[/green] / [yellow]{summary.get('misses', 0)}[/yellow]\n"
        f"restored: [cyan]{restored_entries}[/cyan]\n"
        f"store: [dim]{summary.get('cache_root', '-')}[/dim]"
    )


def _render_activity(settings: ChatSettings, busy: bool, note: str) -> str:
    state = "BUSY" if busy else "READY"
    state_color = "yellow" if busy else "green"
    detail = note or "Type a message, then use History/Mode/Cache without leaving the chat view."
    return (
        f"[bold {state_color}]{state}[/bold {state_color}]"
        f"   [cyan]{settings.session_id}[/cyan]"
        f"   [yellow]{settings.mode}[/yellow]\n"
        f"[dim]{detail}[/dim]"
    )


def _render_workspace_info(settings: ChatSettings, selected_path: str | None, note: str) -> str:
    active_path = selected_path or settings.workspace_root
    return (
        "[bold]Workspace[/bold]\n"
        f"root: [cyan]{settings.workspace_root}[/cyan]\n"
        f"selected: [yellow]{active_path}[/yellow]\n"
        f"[dim]{note}[/dim]"
    )


def _render_artifact_lines(payload: dict[str, Any]) -> list[str]:
    artifacts = payload.get("artifacts") or []
    if not isinstance(artifacts, list):
        return []

    lines: list[str] = []
    for index, artifact in enumerate(artifacts, start=1):
        if not isinstance(artifact, dict):
            continue
        title = str(artifact.get("title") or f"Artifact {index}")
        kind = str(artifact.get("kind") or "unknown")
        mime = str(artifact.get("mime_type") or "")
        url = str(artifact.get("url") or "").strip()
        if not url:
            metadata = artifact.get("metadata")
            if isinstance(metadata, dict):
                url = str(metadata.get("stored_path") or "").strip()
        descriptor = f"{kind} {mime}".strip()
        line = f"artifact: {title} [{descriptor}]"
        if url:
            line += f" -> {url}"
        lines.append(line)
    return lines


def _verified_write_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    outputs = payload.get("tool_outputs") or {}
    if not isinstance(outputs, dict):
        return None
    sys_output = outputs.get("sys")
    if not isinstance(sys_output, dict):
        return None
    if sys_output.get("kind") != "workspace_write" or not sys_output.get("verified"):
        return None
    return sys_output


def _response_claims_filesystem_write(text: str) -> bool:
    normalized = (text or "").casefold()
    markers = (
        "datei geschrieben",
        "dateien geschrieben",
        "datei angelegt",
        "dateien angelegt",
        "datei wurde angelegt",
        "dateien wurden angelegt",
        "datei wurde geschrieben",
        "dateien wurden geschrieben",
        "ordner angelegt",
        "ordner wurde angelegt",
        "file written",
        "files written",
        "file created",
        "files created",
        "write operation completed",
    )
    return any(marker in normalized for marker in markers)


def _filesystem_evidence_notice(text: str, payload: dict[str, Any]) -> str | None:
    if not _response_claims_filesystem_write(text):
        return None
    verified = _verified_write_from_payload(payload)
    if verified is not None:
        evidence = verified.get("evidence") if isinstance(verified.get("evidence"), dict) else {}
        target = verified.get("target_path") or evidence.get("target_path") or "unknown"
        sha256 = evidence.get("sha256") or "-"
        return (
            f"[VERIFIED FILESYSTEM EVIDENCE] Confirmed target: {target} sha256={sha256}. "
            "No other filesystem claims in the model text are implied by this evidence."
        )
    return (
        "[UNVERIFIED FILESYSTEM CLAIM] No verified WSL write evidence was returned. "
        "The model text is not proof that files exist."
    )


def _guard_unverified_write_claim(text: str, payload: dict[str, Any]) -> str:
    notice = _filesystem_evidence_notice(text, payload)
    return f"{notice}\n\n{text}" if notice else text


def _format_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _format_date(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _file_icon(path: Path) -> str:
    ext = path.suffix.lower()
    icons: dict[str, str] = {
        ".py": "🐍", ".js": "📜", ".ts": "📜", ".json": "📋",
        ".md": "📝", ".txt": "📝", ".yaml": "⚙️", ".yml": "⚙️",
        ".toml": "⚙️", ".cfg": "⚙️", ".sh": "🖥️", ".bat": "🖥️",
        ".html": "🌐", ".css": "🎨",
        ".png": "🖼️", ".jpg": "🖼️", ".jpeg": "🖼️", ".gif": "🖼️",
        ".zip": "📦", ".tar": "📦", ".gz": "📦",
        ".pdf": "📕", ".log": "📋",
    }
    return icons.get(ext, "📄")


def create_chat_app(settings: ChatSettings):
    log = _setup_logging(verbose=getattr(settings, "verbose", False))
    log.info(
        "create_chat_app[v3-no-stat-filter]: base_url=%s session=%s user=%s mode=%s",
        settings.base_url,
        settings.session_id,
        settings.user_id,
        settings.mode,
    )
    (
        App,
        ComposeResult,
        Binding,
        Horizontal,
        Vertical,
        Message,
        Button,
        DirectoryTree,
        Footer,
        Header,
        RichLog,
        Static,
        TextArea,
        DataTable,
    ) = _load_textual_symbols()

    class PromptSubmitted(Message):
        pass

    class PromptTextArea(TextArea):
        BINDINGS = [
            Binding("enter", "submit_prompt", "Send", show=False),
            Binding("shift+enter", "insert_newline", "New line", show=False),
            *TextArea.BINDINGS,
        ]

        def action_submit_prompt(self) -> None:
            self.post_message(PromptSubmitted())

        def action_insert_newline(self) -> None:
            self.insert("\n")

    class FolderTree(DirectoryTree):
        """Directory tree that shows only folders (like the left pane in Windows Explorer)."""

        def filter_paths(self, paths):
            # IMPORTANT: do not call is_file()/is_dir() here.
            # DirectoryTree may invoke this on the UI thread; UNC/WSL stat calls can freeze TUI.
            # Use a fast name-based heuristic instead.
            allow_hidden_dirs = {
                ".config",
                ".local",
                ".cache",
                ".ssh",
                ".git",
            }
            skip_names = {
                "node_modules",
                ".venv",
                ".liara-venv",
                "__pycache__",
            }

            folders = []
            for path in paths:
                name = path.name
                if not name:
                    continue
                if name in skip_names:
                    continue
                if name.startswith(".") and name not in allow_hidden_dirs:
                    # hide dot-files (e.g. .ash_history) and uncommon hidden entries
                    continue
                # fast file-ish heuristic: filenames like foo.ext are usually files
                if path.suffix and not name.startswith("."):
                    continue
                folders.append(path)
            return folders

    class LiaraChatApp(App):
        TITLE = "LIARA Chat"
        SUB_TITLE = "Textual CLI"
        CSS = APP_CSS
        FS_TIMEOUT_SECONDS = 8.0

        BINDINGS = [
            Binding("ctrl+c", "quit", "Quit"),
            Binding("ctrl+l", "clear_log", "Clear"),
            Binding("ctrl+s", "toggle_mode", "Toggle Mode"),
            Binding("ctrl+1", "show_chat_tab", "Chat"),
            Binding("ctrl+2", "show_workspace_tab", "Workspace"),
        ]

        def __init__(self, settings_obj: ChatSettings):
            super().__init__()
            self.settings = settings_obj
            self._log = logging.getLogger("liara.tex_ui")
            self.client = LiaraApiClient(settings_obj)
            self._busy = False
            self._activity_note = "Local state persists. Enter sends, Shift+Enter adds a line."
            self._active_panel = "chat"
            self._selected_workspace_path: str | None = None
            self._workspace_note = "Browse the LIARA home and inspect files locally."
            self._current_dir: Path | None = None
            self._view_mode = "details"  # "details" | "tiles"
            self._restored_entries = 0
            self._metrics = {
                "user_turns": 0,
                "assistant_turns": 0,
                "last_latency_ms": 0.0,
                "provider": "-",
                "model": "-",
                "tools": "-",
            }

        def compose(self):
            yield Header(show_clock=True)
            with Horizontal(id="body"):
                with Vertical(id="sidebar"):
                    yield Static(
                        "[bold cyan]LIARA[/bold cyan]\n"
                        "[bold]Chat Deck[/bold]\n"
                        "[dim]Dense local chat UI[/dim]",
                        id="brand",
                    )
                    yield Static(_render_status(self.settings), id="status")
                    yield Static(_render_runtime(self._metrics), id="runtime")
                    yield Static(_render_cache(self.client.cache_summary(), self._restored_entries), id="cache")
                    yield Static(help_text(), id="commands")
                with Vertical(id="main"):
                    with Horizontal(id="tab_bar"):
                        yield Button("Chat", id="tab_chat_btn", variant="primary")
                        yield Button("Workspace", id="tab_workspace_btn")
                    with Vertical(id="chat_view"):
                        yield Static(_render_activity(self.settings, self._busy, self._activity_note), id="activity")
                        yield RichLog(id="chat_log", markup=True, wrap=True, highlight=True)
                        with Horizontal(id="composer"):
                            yield PromptTextArea(
                                "",
                                placeholder="Ask LIARA or /help\nEnter sends, Shift+Enter adds a line",
                                id="prompt",
                                show_line_numbers=False,
                                soft_wrap=True,
                                compact=True,
                            )
                            yield Button("History", id="history_btn")
                            yield Button(f"Mode: {self.settings.mode}", id="mode_btn")
                            yield Button("Clear Cache", id="cache_btn")
                            yield Button("Send", id="send_btn", variant="primary")
                    with Vertical(id="workspace_view", classes="hidden"):
                        yield Static(
                            _render_workspace_info(self.settings, self._selected_workspace_path, self._workspace_note),
                            id="workspace_info",
                        )
                        with Horizontal(id="workspace_body"):
                            yield FolderTree(self.settings.workspace_root, id="workspace_tree")
                            with Vertical(id="dir_panel"):
                                with Horizontal(id="explorer_toolbar"):
                                    yield Button("\u2261  Details", id="view_details_btn", variant="primary")
                                    yield Button("\u229e  Tiles", id="view_tiles_btn")
                                    yield Static("", id="path_label")
                                yield DataTable(id="details_table", cursor_type="row")
                                yield RichLog(id="tiles_panel", markup=True, wrap=True, classes="hidden")
                                with Vertical(id="preview_pane", classes="hidden"):
                                    yield Static("", id="preview_label")
                                    yield TextArea(
                                        "",
                                        id="file_viewer",
                                        show_line_numbers=True,
                                        soft_wrap=False,
                                        compact=True,
                                    )
            yield Footer()

        def on_mount(self) -> None:
            self._log.info("App mounted. workspace_root=%s", self.settings.workspace_root)
            self.query_one("#prompt", PromptTextArea).focus()
            self._setup_details_table()
            viewer = self.query_one("#file_viewer", TextArea)
            if hasattr(viewer, "read_only"):
                viewer.read_only = True
            self._restore_cached_transcript()
            if self._restored_entries:
                self._log_system(
                    f"Restored {self._restored_entries} cached transcript entries for this session.",
                    persist=False,
                )
            else:
                self._log_system("Welcome to LIARA Textual Chat. Type /help for commands.", persist=False)
            self._log_system(
                f"log file: {getattr(self._log, '_liara_log_file', '(unknown)')}",
                persist=False,
            )
            self._refresh_workspace_view()
            self._refresh_status()

        async def on_unmount(self) -> None:
            await self.client.aclose()

        def action_clear_log(self) -> None:
            self._log.info("Transcript cleared by user.")
            self.query_one("#chat_log", RichLog).clear()
            self.client.clear_transcript()
            self._metrics["user_turns"] = 0
            self._metrics["assistant_turns"] = 0
            self._restored_entries = 0
            self._activity_note = "Transcript cleared locally for this session."
            self._log_system("Transcript cleared.", persist=False)
            self._refresh_status()

        def action_show_chat_tab(self) -> None:
            self._log.debug("Switching to Chat tab.")
            self._active_panel = "chat"
            self._sync_panel_state()
            self.query_one("#prompt", PromptTextArea).focus()

        def action_show_workspace_tab(self) -> None:
            self._log.debug("Switching to Workspace tab.")
            self._active_panel = "workspace"
            self._sync_panel_state()
            self.query_one("#workspace_tree", DirectoryTree).focus()

        def action_toggle_mode(self) -> None:
            self.settings.mode = "chat" if self.settings.mode == "stream" else "stream"
            self.client._save_settings()
            self._refresh_status()
            self._activity_note = f"Mode switched to {self.settings.mode}."
            self._log_system(f"Mode switched to: {self.settings.mode}")

        def action_clear_cache(self) -> None:
            self.client.clear_api_cache()
            self._activity_note = "API-side client cache was cleared."
            self._log_system("API cache cleared.")
            self._refresh_status()

        async def on_button_pressed(self, event) -> None:
            if event.button.id == "tab_chat_btn":
                self.action_show_chat_tab()
            elif event.button.id == "tab_workspace_btn":
                self.action_show_workspace_tab()
            elif event.button.id == "view_details_btn":
                await self._switch_view_mode("details")
            elif event.button.id == "view_tiles_btn":
                await self._switch_view_mode("tiles")
            elif event.button.id == "send_btn":
                await self._submit_prompt()
            elif event.button.id == "history_btn":
                await self._show_history_preview(20)
            elif event.button.id == "mode_btn":
                self.action_toggle_mode()
            elif event.button.id == "cache_btn":
                self.action_clear_cache()

        async def on_directory_tree_directory_selected(self, event) -> None:
            path = Path(str(event.path))
            self._log.info("Folder selected: %s", path)
            self._selected_workspace_path = str(path)
            self._workspace_note = f"Browsing: {path.name}  …"
            self._refresh_status()
            await self._populate_dir_panel(path)

        async def on_data_table_row_selected(self, event) -> None:
            key = event.row_key.value
            if key is None:
                return
            path = Path(str(key))
            self._log.debug("Row selected: %s", path)
            is_dir = await asyncio.to_thread(path.is_dir)
            is_file = await asyncio.to_thread(path.is_file)
            if is_dir:
                self._workspace_note = f"Navigated to: {path.name}  …"
                self._selected_workspace_path = str(path)
                self._refresh_status()
                await self._populate_dir_panel(path)
            elif is_file:
                self._workspace_note = f"Previewing: {path.name}  …"
                self._selected_workspace_path = str(path)
                self._refresh_status()
                await self._show_file_preview(path)

        async def on_prompt_submitted(self, _message: PromptSubmitted) -> None:
            if self.screen.focused is self.query_one("#prompt", PromptTextArea):
                await self._submit_prompt()

        def _restore_cached_transcript(self) -> None:
            entries = self.client.get_cached_transcript()
            self._restored_entries = len(entries)
            for entry in entries:
                role = str(entry.get("role", "system"))
                text = str(entry.get("text", ""))
                if not text:
                    continue
                self._write_message(role, text, persist=False)
                if role == "user":
                    self._metrics["user_turns"] += 1
                elif role == "assistant":
                    self._metrics["assistant_turns"] += 1

        async def _submit_prompt(self) -> None:
            if self._busy:
                return
            prompt = self.query_one("#prompt", PromptTextArea)
            message = prompt.text.strip()
            if not message:
                return
            prompt.load_text("")

            parsed = parse_command(message)
            if parsed is not None:
                await self._run_command(parsed)
                return

            await self._run_chat(message)

        async def _run_command(self, parsed: ParsedCommand) -> None:
            name = parsed.name
            if name == "/help":
                self._log_system(help_text())
                return
            if name == "/clear":
                self.action_clear_log()
                return
            if name == "/quit":
                self.exit()
                return
            if name == "/mode":
                requested = parsed.argument.lower()
                if requested not in {"chat", "stream"}:
                    self._log_system("Usage: /mode chat|stream")
                    return
                self.settings.mode = requested  # type: ignore[assignment]
                self.client._save_settings()
                self._refresh_status()
                self._log_system(f"Mode set to: {requested}")
                return
            if name == "/max-tokens":
                try:
                    candidate = int(parsed.argument)
                except ValueError:
                    self._log_system("Usage: /max-tokens <number>")
                    return
                if candidate < 256:
                    self._log_system("max-tokens must be >= 256")
                    return
                self.settings.max_tokens = candidate
                self.client._save_settings()
                self._refresh_status()
                self._log_system(f"max_tokens updated: {candidate}")
                return
            if name == "/history":
                limit = 20
                if parsed.argument:
                    try:
                        limit = max(1, min(200, int(parsed.argument)))
                    except ValueError:
                        self._log_system("Usage: /history [limit]")
                        return
                await self._show_history_preview(limit)
                return
            if name == "/health":
                try:
                    data = await self.client.get_health()
                    state = data.get("status", "unknown")
                    service = data.get("service", "liara-api")
                    self._activity_note = f"Health request completed for {service}."
                    self._log_system(f"health: {service} -> {state}")
                    self._refresh_status()
                except Exception as exc:
                    self._log_system(f"health failed: {exc}")
                return
            if name == "/tools":
                try:
                    data = await self.client.get_tools()
                    tools = data.get("tools") or []
                    names = [str(item.get("name", "?")) for item in tools]
                    self._activity_note = f"Loaded tool catalog with {len(names)} items."
                    self._log_system(f"tools ({len(names)}): {', '.join(names) if names else '-'}")
                    self._refresh_status()
                except Exception as exc:
                    self._log_system(f"tools failed: {exc}")
                return
            if name == "/sys":
                await self._run_sys(parsed.argument)
                return
            if name == "/cache":
                requested = (parsed.argument or "stats").strip().lower()
                if requested == "clear":
                    self.action_clear_cache()
                    return
                if requested not in {"stats", ""}:
                    self._log_system("Usage: /cache [stats|clear]")
                    return
                summary = self.client.cache_summary()
                self._log_system(
                    "cache: "
                    f"api={summary.get('api_entries', 0)} "
                    f"transcripts={summary.get('transcript_sessions', 0)} "
                    f"hits={summary.get('hits', 0)} misses={summary.get('misses', 0)}"
                )
                self._refresh_status()
                return

            self._log_system(f"Unknown command: {name}")

        async def _run_sys(self, argument: str) -> None:
            try:
                invocation = parse_sys_invocation(argument)
            except ValueError as exc:
                self._log_system(str(exc))
                return

            self._busy = True
            self._set_input_enabled(False)
            self._activity_note = f"Executing policy-gated /sys {invocation.command}..."
            self._refresh_status()
            try:
                result = await self.client.invoke_sys(
                    invocation.command,
                    invocation.args,
                    stdin_text=invocation.stdin_text,
                )
                status = str(result.get("status") or "failed")
                metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
                if status != "success":
                    self._activity_note = "Sys command failed."
                    self._log_system(f"sys failed: {result.get('error') or 'unknown error'}")
                    return

                is_write = invocation.command in {"tee", "mkdir", "touch"}
                if is_write and not metadata.get("mutation_verified"):
                    self._activity_note = "Sys mutation was not verified."
                    self._log_system("sys write rejected: no verified WSL mutation evidence returned")
                    return

                evidence = metadata.get("mutation_evidence") or {}
                if is_write:
                    target = evidence.get("target_path") or metadata.get("target_path") or "-"
                    sha256 = evidence.get("sha256") or "-"
                    self._log_system(f"sys verified: {invocation.command} -> {target} sha256={sha256}")
                    self._activity_note = f"Verified WSL mutation: {target}"
                    if self._current_dir is not None:
                        await self._populate_dir_panel(self._current_dir)
                else:
                    output = str(result.get("output") or "").strip()
                    self._log_system(f"sys success: {output or '(empty output)'}")
                    self._activity_note = f"Sys command completed: {invocation.command}"
            except Exception as exc:
                self._activity_note = "Sys request failed."
                self._log.exception("Sys request failed: %s", exc)
                self._log_system(f"sys failed: {exc}")
            finally:
                self._busy = False
                self._set_input_enabled(True)
                self._refresh_status()
                self.query_one("#prompt", PromptTextArea).focus()

        async def _show_history_preview(self, limit: int) -> None:
            try:
                data = await self.client.get_history(self.settings.session_id, limit)
                items = data.get("items", [])
                self._activity_note = f"Fetched session history preview ({len(items)} entries)."
                self._log_system(f"history entries: {len(items)}")
                for item in items[-limit:]:
                    role = str(item.get("role", "unknown")).upper()
                    content = str(item.get("content", "")).strip().replace("\n", " ")
                    preview = content[:140]
                    self._log_system(f"[{role}] {preview}")
                self._refresh_status()
            except Exception as exc:
                self._log_system(f"history failed: {exc}")

        async def _run_chat(self, message: str) -> None:
            self._log.info("User message sent (mode=%s, len=%d)", self.settings.mode, len(message))
            self._busy = True
            started = perf_counter()
            self._set_input_enabled(False)
            self._activity_note = "Sending prompt to liara-api..."
            self._refresh_status()
            self._log_user(message)
            try:
                if self.settings.mode == "stream":
                    log = self.query_one("#chat_log", RichLog)
                    assistant_header_written = False

                    def _on_chunk(chunk_text: str) -> None:
                        nonlocal assistant_header_written
                        if not assistant_header_written:
                            log.write("[message-assistant][ LIARA ][/message-assistant]")
                            assistant_header_written = True
                        log.write(chunk_text, scroll_end=True)

                    def _on_progress(payload: dict[str, Any]) -> None:
                        stage = str(payload.get("stage") or "")
                        if stage == "orchestration_complete":
                            self._activity_note = "Model is streaming response..."
                            self._refresh_status()

                    reply = await self.client.send_stream(
                        message,
                        on_chunk=_on_chunk,
                        on_progress=_on_progress,
                    )
                else:
                    reply = await self.client.send_chat(message)
                raw_answer = (reply.text or "").strip() or "(no response)"
                evidence_notice = _filesystem_evidence_notice(raw_answer, reply.payload)
                answer = _guard_unverified_write_claim(raw_answer, reply.payload)
                self._metrics["last_latency_ms"] = (perf_counter() - started) * 1000.0
                self._metrics["provider"] = str(reply.payload.get("llm_provider", "-"))
                self._metrics["model"] = str(reply.payload.get("llm_model", "-"))
                used_tools = reply.payload.get("tools_used") or []
                self._metrics["tools"] = ", ".join(str(item) for item in used_tools) if used_tools else "-"
                self._activity_note = "Assistant response received and persisted in the local transcript cache."
                if not bool(reply.payload.get("validation_passed", True)):
                    self._activity_note = "Assistant response received with validator warning/block status."
                self._log.info(
                    "Reply received: provider=%s model=%s latency=%.0fms",
                    reply.payload.get("llm_provider", "-"),
                    reply.payload.get("llm_model", "-"),
                    self._metrics["last_latency_ms"],
                )
                if self.settings.mode == "stream":
                    if evidence_notice:
                        self._log_system(evidence_notice, persist=False)
                    self._metrics["assistant_turns"] += 1
                    self.client.append_transcript("assistant", answer, kind="chat")
                else:
                    self._log_assistant(answer)

                if not bool(reply.payload.get("validation_passed", True)):
                    validation = (reply.payload.get("metadata") or {}).get("validation") or {}
                    decision = validation.get("decision") if isinstance(validation, dict) else None
                    self._log_system(
                        f"validator warning: validation_passed=false decision={decision or 'unknown'}",
                        persist=False,
                    )

                for artifact_line in _render_artifact_lines(reply.payload):
                    self._log_system(artifact_line, persist=False)

                if self.settings.verbose:
                    provider = reply.payload.get("llm_provider", "-")
                    model = reply.payload.get("llm_model", "-")
                    self._log_system(f"provider={provider} model={model}")
            except Exception as exc:
                self._activity_note = "Chat request failed."
                self._log.exception("Chat request failed: %s", exc)
                self._log_system(f"chat failed: {exc}")
            finally:
                self._busy = False
                self._set_input_enabled(True)
                self._refresh_status()
                self.query_one("#prompt", PromptTextArea).focus()

        def _set_input_enabled(self, enabled: bool) -> None:
            self.query_one("#prompt", PromptTextArea).disabled = not enabled
            self.query_one("#send_btn", Button).disabled = not enabled
            self.query_one("#history_btn", Button).disabled = not enabled

        def _sync_panel_state(self) -> None:
            chat_view = self.query_one("#chat_view", Vertical)
            workspace_view = self.query_one("#workspace_view", Vertical)
            chat_btn = self.query_one("#tab_chat_btn", Button)
            workspace_btn = self.query_one("#tab_workspace_btn", Button)

            if self._active_panel == "chat":
                chat_view.remove_class("hidden")
                workspace_view.add_class("hidden")
                chat_btn.variant = "primary"
                workspace_btn.variant = "default"
            else:
                workspace_view.remove_class("hidden")
                chat_view.add_class("hidden")
                workspace_btn.variant = "primary"
                chat_btn.variant = "default"

        def _refresh_workspace_view(self) -> None:
            self.query_one("#workspace_info", Static).update(
                _render_workspace_info(self.settings, self._selected_workspace_path, self._workspace_note)
            )

        def _setup_details_table(self) -> None:
            table = self.query_one("#details_table", DataTable)
            table.add_columns("", "Name", "Size", "Modified", "Type")

        async def _populate_dir_panel(self, path: Path) -> None:
            self._current_dir = path
            self.query_one("#path_label", Static).update(f"[dim]{path}[/dim]")
            self.query_one("#preview_pane", Vertical).add_class("hidden")
            if self._view_mode == "details":
                await self._refresh_details_table(path)
            else:
                await self._refresh_tiles_view(path)

        async def _refresh_details_table(self, path: Path) -> None:
            self._log.debug("Loading details table for: %s", path)
            def _collect() -> list:
                try:
                    rows = []
                    for entry in sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
                        icon = "DIR" if entry.is_dir() else "FILE"
                        try:
                            st = entry.stat()
                            size = "-" if entry.is_dir() else _format_size(st.st_size)
                            modified = _format_date(st.st_mtime)
                        except OSError:
                            size = "-"
                            modified = "-"
                        kind = "Folder" if entry.is_dir() else (entry.suffix[1:].upper() or "File")
                        rows.append((icon, entry.name, size, modified, kind, str(entry)))
                    return rows
                except PermissionError:
                    return [("🔒", "(permission denied)", "", "", "", None)]

            try:
                rows = await asyncio.wait_for(
                    asyncio.to_thread(_collect),
                    timeout=self.FS_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                self._log.warning("Details table timeout after %.1fs for %s", self.FS_TIMEOUT_SECONDS, path)
                rows = [("TIME", "(scan timeout)", "", "", "", None)]
            self._log.debug("Details table: %d row(s) from %s", len(rows), path)
            table = self.query_one("#details_table", DataTable)
            table.clear()
            for icon, name, size, modified, kind, key in rows:
                table.add_row(icon, name, size, modified, kind, key=key)
            if len(rows) == 1 and rows[0][1] == "(scan timeout)":
                self._workspace_note = f"Directory scan timeout for {path.name}."
            else:
                self._workspace_note = f"{len(rows)} item(s) in {path.name}"
            self._refresh_status()

        async def _refresh_tiles_view(self, path: Path) -> None:
            from rich.columns import Columns
            from rich.text import Text

            def _collect():
                try:
                    return sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                except PermissionError:
                    return None

            try:
                entries = await asyncio.wait_for(
                    asyncio.to_thread(_collect),
                    timeout=self.FS_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                self._log.warning("Tiles scan timeout after %.1fs for %s", self.FS_TIMEOUT_SECONDS, path)
                entries = None
            log = self.query_one("#tiles_panel", RichLog)
            log.clear()
            if entries is None:
                log.write("[red]⏱ Timeout or permission issue while reading directory[/red]")
                self._workspace_note = f"Directory scan timeout for {path.name}."
                self._refresh_status()
                return
            if not entries:
                log.write("[dim](empty directory)[/dim]")
                return
            tiles = []
            for entry in entries:
                icon = "\ud83d\udcc1" if entry.is_dir() else _file_icon(entry)
                name = entry.name if len(entry.name) <= 18 else entry.name[:16] + "\u2026"
                color = "cyan" if entry.is_dir() else "white"
                tiles.append(Text(f"{icon} {name}", style=color))
            log.write(Columns(tiles, equal=True, expand=True))
            self._workspace_note = f"{len(entries)} item(s) in {path.name}"
            self._refresh_status()

        async def _switch_view_mode(self, mode: str) -> None:
            self._view_mode = mode
            table = self.query_one("#details_table", DataTable)
            tiles = self.query_one("#tiles_panel", RichLog)
            details_btn = self.query_one("#view_details_btn", Button)
            tiles_btn = self.query_one("#view_tiles_btn", Button)
            if mode == "details":
                table.remove_class("hidden")
                tiles.add_class("hidden")
                details_btn.variant = "primary"
                tiles_btn.variant = "default"
                if self._current_dir:
                    await self._refresh_details_table(self._current_dir)
            else:
                table.add_class("hidden")
                tiles.remove_class("hidden")
                tiles_btn.variant = "primary"
                details_btn.variant = "default"
                if self._current_dir:
                    await self._refresh_tiles_view(self._current_dir)

        async def _show_file_preview(self, path: Path) -> None:
            self._log.info("Opening file preview: %s", path)
            pane = self.query_one("#preview_pane", Vertical)
            pane.remove_class("hidden")
            try:
                size_str = await asyncio.wait_for(
                    asyncio.to_thread(lambda: _format_size(path.stat().st_size)),
                    timeout=self.FS_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                size_str = "timeout"
                self._log.warning("File stat timeout after %.1fs for %s", self.FS_TIMEOUT_SECONDS, path)
            except OSError:
                size_str = "?"
            self.query_one("#preview_label", Static).update(
                f"[cyan]{path.name}[/cyan]  [dim]{size_str}[/dim]"
            )
            viewer = self.query_one("#file_viewer", TextArea)
            await self._load_file_to_viewer(path, viewer)

        async def _load_file_to_viewer(self, path: Path, viewer) -> None:
            def _read():
                if not path.is_file():
                    return None, f"{path}\n\nNot a regular file."
                raw = path.read_bytes()
                size = len(raw)
                truncated = size > 256_000
                if truncated:
                    raw = raw[:256_000]
                if b"\x00" in raw:
                    return "binary", None
                text = raw.decode("utf-8", errors="replace")
                if truncated:
                    text += "\n\n[truncated after 256 KB]"
                return "text", text

            try:
                kind, content = await asyncio.wait_for(
                    asyncio.to_thread(_read),
                    timeout=self.FS_TIMEOUT_SECONDS,
                )
                self._log.debug("File read result: kind=%s path=%s", kind, path)
                if kind == "binary":
                    viewer.load_text("Binary file \u2014 text preview not supported.")
                    self._workspace_note = "Binary file selected."
                elif content is not None:
                    viewer.load_text(content)
                    self._workspace_note = f"Previewing: {path.name}"
                else:
                    viewer.load_text(content or "")
            except asyncio.TimeoutError:
                self._log.warning("File read timeout after %.1fs for %s", self.FS_TIMEOUT_SECONDS, path)
                viewer.load_text(f"Timeout while reading file:\n{path}")
                self._workspace_note = f"Timeout while opening {path.name}."
            except Exception as exc:
                self._log.exception("Error reading file %s: %s", path, exc)
                viewer.load_text(f"Failed to read file:\n{path}\n\n{exc}")
                self._workspace_note = f"Failed to open {path.name}."
            self._refresh_status()

        def _refresh_status(self) -> None:
            self.query_one("#status", Static).update(_render_status(self.settings))
            self.query_one("#runtime", Static).update(_render_runtime(self._metrics))
            self.query_one("#cache", Static).update(
                _render_cache(self.client.cache_summary(), self._restored_entries)
            )
            self.query_one("#activity", Static).update(
                _render_activity(self.settings, self._busy, self._activity_note)
            )
            self.query_one("#mode_btn", Button).label = f"Mode: {self.settings.mode}"
            self._refresh_workspace_view()

        def _log_user(self, text: str) -> None:
            self._metrics["user_turns"] += 1
            self._write_message("user", text)

        def _log_assistant(self, text: str) -> None:
            self._metrics["assistant_turns"] += 1
            self._write_message("assistant", text)

        def _log_system(self, text: str, persist: bool = True) -> None:
            self._write_message("system", text, persist=persist, markdown=False)

        def _write_message(
            self,
            role: str,
            text: str,
            *,
            persist: bool = True,
            markdown: bool = True,
        ) -> None:
            log = self.query_one("#chat_log", RichLog)
            if role == "user":
                log.write("[message-user][ YOU ][/message-user]")
                log.write(Markdown(text) if markdown else text)
            elif role == "assistant":
                log.write("[message-assistant][ LIARA ][/message-assistant]")
                log.write(Markdown(text) if markdown else text)
            else:
                log.write(f"[message-system]{text}[/message-system]")

            if persist:
                self.client.append_transcript(role, text, kind="system" if role == "system" else "chat")

    return LiaraChatApp(settings)


def run_textual_chat(settings: ChatSettings) -> int:
    app = create_chat_app(settings)
    app.run()
    return 0
