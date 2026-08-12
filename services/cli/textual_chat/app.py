from __future__ import annotations

import importlib
from time import perf_counter
from typing import Any

from rich.markdown import Markdown

from .client import LiaraApiClient
from .commands import ParsedCommand, help_text, parse_command
from .models import ChatMode, ChatSettings
from .theme import APP_CSS


# Thread-safe event handling for progress updates
_progress_events = []


def _record_progress_event(event: dict[str, Any]) -> None:
    """Record a progress event for async processing by the UI."""
    _progress_events.append(event)


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
    Footer = getattr(widgets_mod, "Footer")
    Header = getattr(widgets_mod, "Header")
    RichLog = getattr(widgets_mod, "RichLog")
    Static = getattr(widgets_mod, "Static")
    TextArea = getattr(widgets_mod, "TextArea")

    return App, ComposeResult, Binding, Horizontal, Vertical, Message, Button, Footer, Header, RichLog, Static, TextArea


def _render_status(settings: ChatSettings) -> str:
    return (
        "[bold]Session[/bold]\n"
        f"id: [cyan]{settings.session_id}[/cyan]\n"
        f"user: [cyan]{settings.user_id}[/cyan]\n"
        f"mode: [yellow]{settings.mode}[/yellow]  max: [magenta]{settings.max_tokens}[/magenta]\n"
        f"url: [dim]{settings.base_url}[/dim]"
    )


def _render_runtime(metrics: dict[str, Any]) -> str:
    latency = metrics.get("last_latency_ms")
    latency_text = "-" if latency in (None, 0, 0.0) else f"{float(latency):.0f} ms"
    context_mode = metrics.get("last_context_mode", "-")
    memory_indicator = "✨" if context_mode == "MEMORY" else " "
    return (
        "[bold]Runtime[/bold]\n"
        f"turns: [cyan]{metrics.get('user_turns', 0)}[/cyan] / [cyan]{metrics.get('assistant_turns', 0)}[/cyan]\n"
        f"latency: [green]{latency_text}[/green]\n"
        f"provider: [cyan]{metrics.get('provider', '-')}[/cyan]\n"
        f"model: [cyan]{metrics.get('model', '-')}[/cyan]\n"
        f"memory: {memory_indicator} [yellow]{context_mode}[/yellow]\n"
        f"tools: [dim]{metrics.get('tools', '-')}[/dim]\n"
        f"agent: [cyan]{metrics.get('agent_steps', '-')}[/cyan] "
        f"[yellow]{metrics.get('agent_status', '-')}[/yellow]\n"
        f"budget: [dim]{metrics.get('agent_budget', '-')}[/dim]\n"
        f"validator: [dim]{metrics.get('agent_validator', '-')}[/dim]"
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


def create_chat_app(settings: ChatSettings):
    (
        App,
        ComposeResult,
        Binding,
        Horizontal,
        Vertical,
        Message,
        Button,
        Footer,
        Header,
        RichLog,
        Static,
        TextArea,
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

    class LiaraChatApp(App):
        TITLE = "LIARA Chat"
        SUB_TITLE = "Textual CLI"
        CSS = APP_CSS

        BINDINGS = [
            Binding("ctrl+c", "quit", "Quit"),
            Binding("ctrl+l", "clear_log", "Clear"),
            Binding("ctrl+s", "toggle_mode", "Toggle Mode"),
        ]

        def __init__(self, settings_obj: ChatSettings):
            super().__init__()
            self.settings = settings_obj
            self.client = LiaraApiClient(settings_obj)
            self._busy = False
            self._activity_note = "Local state persists. Enter sends, Shift+Enter adds a line."
            self._restored_entries = 0
            self._metrics = {
                "user_turns": 0,
                "assistant_turns": 0,
                "last_latency_ms": 0.0,
                "last_context_mode": "-",
                "provider": "-",
                "model": "-",
                "tools": "-",
                "agent_status": "-",
                "agent_steps": "-",
                "agent_validator": "-",
                "agent_budget": "-",
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
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#prompt", PromptTextArea).focus()
            self._restore_cached_transcript()
            if self._restored_entries:
                self._log_system(
                    f"Restored {self._restored_entries} cached transcript entries for this session.",
                    persist=False,
                )
            else:
                self._log_system("Welcome to LIARA Textual Chat. Type /help for commands.", persist=False)
            self._refresh_status()

        async def on_unmount(self) -> None:
            await self.client.aclose()

        def action_clear_log(self) -> None:
            self.query_one("#chat_log", RichLog).clear()
            self.client.clear_transcript()
            self._metrics["user_turns"] = 0
            self._metrics["assistant_turns"] = 0
            self._restored_entries = 0
            self._activity_note = "Transcript cleared locally for this session."
            self._log_system("Transcript cleared.", persist=False)
            self._refresh_status()

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
            if event.button.id == "send_btn":
                await self._submit_prompt()
            elif event.button.id == "history_btn":
                await self._show_history_preview(20)
            elif event.button.id == "mode_btn":
                self.action_toggle_mode()
            elif event.button.id == "cache_btn":
                self.action_clear_cache()

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
            if self._busy:
                return
            self._busy = True
            started = perf_counter()
            self._set_input_enabled(False)
            self._activity_note = "Sending prompt to liara-api..."
            self._refresh_status()
            self._log_user(message)
            
            try:
                def on_progress(event: dict[str, Any]) -> None:
                    stage = event.get("stage", "")
                    if stage == "memory_effect_detected":
                        context_mode = (event.get("metadata") or {}).get("context_mode", "unknown")
                        self._activity_note = f"✨ Memory effect detected (context_mode={context_mode})"
                        self._refresh_status()
                    elif stage == "orchestration_complete":
                        self._activity_note = "Planning complete, streaming response..."
                        self._refresh_status()
                
                if self.settings.mode == "stream":
                    reply = await self.client.send_stream(message, progress_callback=on_progress)
                else:
                    reply = await self.client.send_chat(message)
                    
                answer = (reply.text or "").strip() or "(no response)"
                self._metrics["last_latency_ms"] = (perf_counter() - started) * 1000.0
                self._metrics["provider"] = str(reply.payload.get("llm_provider", "-"))
                self._metrics["model"] = str(reply.payload.get("llm_model", "-"))
                used_tools = reply.payload.get("tools_used") or []
                self._metrics["tools"] = ", ".join(str(item) for item in used_tools) if used_tools else "-"
                workspace_run = (reply.payload.get("tool_outputs") or {}).get("workspace_agent") or {}
                if isinstance(workspace_run, dict) and workspace_run:
                    steps = workspace_run.get("steps") or []
                    planned = ((workspace_run.get("plan") or {}).get("steps") or [])
                    self._metrics["agent_status"] = str(workspace_run.get("status") or "unknown")
                    self._metrics["agent_steps"] = f"{len(steps)}/{len(planned)}"
                    validator = workspace_run.get("validator") or {}
                    self._metrics["agent_validator"] = str(validator.get("state") or "not-run")
                    last_math = dict((steps[-1] if steps else {}).get("math") or {})
                    if last_math:
                        self._metrics["agent_budget"] = (
                            f"C={last_math.get('cost_total', '-')} "
                            f"U={last_math.get('utility', '-')} "
                            f"{last_math.get('control_mode', '-')} "
                            f"[{last_math.get('compute_backend', '-')}:{last_math.get('compute_path', '-')}]"
                        )
                    self._log_system(
                        f"workspace-agent status={self._metrics['agent_status']} "
                        f"steps={self._metrics['agent_steps']} "
                        f"validator={self._metrics['agent_validator']}"
                    )
                
                # Check if memory was used
                context_mode = (reply.payload.get("metadata") or {}).get("context_debug", {}).get("mode", "-")
                self._metrics["last_context_mode"] = context_mode
                self._activity_note = f"Response received | context_mode={context_mode}"
                
                self._log_assistant(answer)
                if self.settings.verbose:
                    provider = reply.payload.get("llm_provider", "-")
                    model = reply.payload.get("llm_model", "-")
                    self._log_system(f"provider={provider} model={model} context_mode={context_mode}")
            except Exception as exc:
                self._activity_note = "Chat request failed."
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
