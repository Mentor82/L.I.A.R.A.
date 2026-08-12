"""Terminal CLI for liara-api.

Usage examples:
  python -m services.cli.main chat "Wie spaet ist es?"
  python -m services.cli.main stream "Erzaehl mir etwas ueber Liara"
  python -m services.cli.main repl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from difflib import get_close_matches
from typing import Any
from urllib.parse import urlparse

import httpx
from rich.console import Console
from rich.json import JSON as RichJSON
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from services.cli.holo.live_view import VALID_HOLO_MODES, run_holo_live
from services.cli.textual_chat.models import ChatSettings
from services.cli.textual_chat import run_textual_chat


console = Console()

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_TRANSPORT = 3
EXIT_VALIDATION = 4
EXIT_BLOCKED = 5

DEFAULT_BASE_URL = os.getenv("LIARA_API_BASE_URL", "http://127.0.0.1:8010")
DEFAULT_USER_ID = os.getenv("LIARA_USER_ID", "cli-user")


def _default_timeout() -> float:
    """Return HTTP timeout default with a safer fallback for local inference latency."""
    raw_value = os.getenv("LIARA_HTTP_TIMEOUT", "90")
    try:
        return max(5.0, float(raw_value))
    except ValueError:
        return 90.0


def _default_max_tokens() -> int:
    """Return CLI max token default with a safe fallback for longer answers."""
    raw_value = os.getenv("LIARA_MAX_TOKENS", "32768")
    try:
        return max(256, int(raw_value))
    except ValueError:
        return 32768


DEFAULT_MAX_TOKENS = _default_max_tokens()
DEFAULT_HTTP_TIMEOUT = _default_timeout()
STREAM_MARKDOWN_RENDER = os.getenv("LIARA_STREAM_MARKDOWN_RENDER", "true").lower() == "true"


def _is_json_output(args: argparse.Namespace) -> bool:
    return getattr(args, "output", "human") == "json"


def _emit_json(payload: dict[str, Any], *, stream=None) -> None:
    # ASCII escaping keeps the machine contract independent of the active
    # Windows console code page while preserving Unicode after JSON parsing.
    print(json.dumps(payload, ensure_ascii=True, sort_keys=True), file=stream or sys.stdout)


def _emit_error(
    args: argparse.Namespace,
    *,
    error_type: str,
    message: str,
    exit_code: int,
) -> int:
    if _is_json_output(args):
        _emit_json(
            {
                "schema_version": "liara.cli.v1",
                "ok": False,
                "command": getattr(args, "command", None),
                "error": {"type": error_type, "message": message},
                "exit_code": exit_code,
            },
            stream=sys.stderr,
        )
    else:
        print(message, file=sys.stderr)
    return exit_code


def _validation_exit_code(payload: dict[str, Any], fail_on_validation: bool) -> int:
    if not fail_on_validation:
        return EXIT_OK
    metadata = payload.get("metadata") or {}
    validation = metadata.get("validation") or {}
    decision = str(validation.get("decision") or "").strip().lower()
    if decision == "block":
        return EXIT_BLOCKED
    if decision in {"warn", "revise"} or payload.get("validation_passed") is False:
        return EXIT_VALIDATION
    return EXIT_OK


def _machine_payload(
    payload: dict[str, Any],
    *,
    args: argparse.Namespace,
    requested_command: str,
    transport: str,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    result = dict(payload)
    result.setdefault("session_id", getattr(args, "session_id", None))
    result["schema_version"] = "liara.cli.v1"
    result["ok"] = _validation_exit_code(
        payload,
        bool(getattr(args, "fail_on_validation", False)),
    ) == EXIT_OK
    result["_cli"] = {
        "requested_command": requested_command,
        "transport": transport,
        "fallback": fallback_reason is not None,
        "fallback_reason": fallback_reason,
    }
    return result


def _default_session_id() -> str:
    """Return the last-used session ID or generate a new one."""
    return _load_session_id() or f"session-{uuid.uuid4().hex[:8]}"


_LIARA_DIR = os.path.join(os.path.expanduser("~"), ".liara")
_SESSION_FILE = os.path.join(_LIARA_DIR, "session.json")


def _load_session_id() -> str | None:
    """Load the last-used session ID from ~/.liara/session.json."""
    try:
        with open(_SESSION_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
            sid = data.get("session_id")
            return str(sid) if sid else None
    except (OSError, json.JSONDecodeError, KeyError):
        return None


def _save_session_id(session_id: str) -> None:
    """Persist the active session ID to ~/.liara/session.json."""
    try:
        os.makedirs(_LIARA_DIR, exist_ok=True)
        with open(_SESSION_FILE, "w", encoding="utf-8") as fh:
            json.dump({"session_id": session_id}, fh)
    except OSError:
        pass  # best-effort


def _client(base_url: str, timeout: float) -> httpx.Client:
    return httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)


def _uvicorn_hint_from_base_url(base_url: str) -> str:
    """Build a start command hint from the configured API URL."""
    parsed = urlparse(base_url)
    host = os.getenv("LIARA_API_BIND_HOST", "0.0.0.0")
    port = parsed.port or 8010
    return f"python -m uvicorn services.api.app:app --host {host} --port {port}"


def _repl_startup_preflight(base_url: str, timeout: float) -> bool:
    """Check API availability once when REPL starts and show a useful hint if offline."""
    probe_timeout = max(2.0, min(timeout, 5.0))
    try:
        with _client(base_url, probe_timeout) as client:
            response = client.get("/health")
            response.raise_for_status()
        return True
    except Exception as exc:
        hint = _uvicorn_hint_from_base_url(base_url)
        console.print(
            Panel(
                f"[yellow]API seems offline:[/yellow] {exc}\n"
                f"[dim]Try starting it with:[/dim]\n[bold]{hint}[/bold]",
                title="[bold yellow]Startup Check[/bold yellow]",
                border_style="yellow",
            )
        )
        return False


def _chat_payload(
    session_id: str,
    user_id: str,
    message: str,
    max_tokens: int,
    preferred_provider: str | None = None,
    preferred_model: str | None = None,
) -> dict[str, Any]:
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "message": message,
        "max_tokens": max_tokens,
    }
    if preferred_provider:
        payload["preferred_provider"] = preferred_provider
    if preferred_model:
        payload["preferred_model"] = preferred_model
    return payload


def _print_assistant_response(response_text: str) -> None:
    """Render assistant text with markdown support for consistent CLI formatting."""
    text = response_text or ""
    if not text.strip():
        text = "(no response)"
    console.print(
        Panel(
            Markdown(text),
            title="[bold cyan]Assistant[/bold cyan]",
            border_style="cyan",
        )
    )


def _print_validation_debug(payload: dict[str, Any]) -> None:
    """Print compact debug lines for mode/context sources/validator decision."""
    metadata = payload.get("metadata") or {}
    context_debug = metadata.get("context_debug") or {}
    validation = metadata.get("validation") or {}

    mode = str(context_debug.get("mode") or "NONE").upper()
    sources = context_debug.get("sources") or {}
    chroma_count = int(sources.get("chroma", 0) or 0)
    qdrant_count = int(sources.get("qdrant", 0) or 0)
    postgres_count = int(sources.get("postgres", 0) or 0)

    decision = validation.get("decision")
    if not decision:
        decision = "accept" if payload.get("validation_passed") else "revise"

    console.print(f"[dim][MODE][/dim] [bold]{mode}[/bold]")
    console.print(
        f"[dim][CTX][/dim] chroma: {chroma_count} | qdrant: {qdrant_count} | postgres: {postgres_count}"
    )
    console.print(f"[dim][VAL][/dim] [bold]{str(decision).lower()}[/bold]")


def cmd_chat(
    args: argparse.Namespace,
    *,
    requested_command: str = "chat",
    fallback_reason: str | None = None,
) -> int:
    payload = _chat_payload(
        args.session_id,
        args.user_id,
        args.message,
        args.max_tokens,
        preferred_provider=getattr(args, "preferred_provider", None),
        preferred_model=getattr(args, "preferred_model", None),
    )
    try:
        with _client(args.base_url, args.timeout) as client:
            response = client.post("/chat", json=payload)
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        return _emit_error(
            args,
            error_type="transport_error",
            message=f"Chat request failed: {exc}",
            exit_code=EXIT_TRANSPORT,
        )

    _save_session_id(args.session_id)
    exit_code = _validation_exit_code(data, bool(getattr(args, "fail_on_validation", False)))
    if _is_json_output(args):
        _emit_json(
            _machine_payload(
                data,
                args=args,
                requested_command=requested_command,
                transport="chat",
                fallback_reason=fallback_reason,
            )
        )
        return exit_code

    # Pretty print response with border
    _print_assistant_response(data.get("response", ""))
    _print_validation_debug(data)
    
    if args.verbose:
        metadata = {
            "run_id": data.get("run_id"),
            "provider": data.get("llm_provider"),
            "model": data.get("llm_model"),
            "tools_used": data.get("tools_used", []),
        }
        console.print(RichJSON.from_data(metadata))
        console.print("[dim]💡 Tip: Use /context to see what messages were loaded into the LLM prompt[/dim]")
    return exit_code


def _iter_sse_lines(response: httpx.Response):
    for line in response.iter_lines():
        if line is None:
            continue
        text = line.decode("utf-8") if isinstance(line, bytes) else line
        yield text


def cmd_stream(args: argparse.Namespace) -> int:
    """Stream chat response via SSE. Falls back to chat if stream is empty/fails."""
    payload = _chat_payload(
        args.session_id,
        args.user_id,
        args.message,
        args.max_tokens,
        preferred_provider=getattr(args, "preferred_provider", None),
        preferred_model=getattr(args, "preferred_model", None),
    )
    streamed_parts: list[str] = []
    final_payload: dict[str, Any] | None = None
    progress_count = 0
    
    try:
        with _client(args.base_url, args.timeout) as client:
            chunk_count = 0
            heartbeat_count = 0
            with client.stream("POST", "/chat/stream", json=payload) as response:
                response.raise_for_status()
                current_event = None
                for line in _iter_sse_lines(response):
                    if not line:
                        continue
                    if line.startswith("event:"):
                        current_event = line.split(":", 1)[1].strip()
                        continue
                    if not line.startswith("data:"):
                        continue

                    raw_data = line.split(":", 1)[1].strip()
                    if current_event == "chunk":
                        chunk_count += 1
                        payload_obj = json.loads(raw_data)
                        chunk_text = payload_obj.get("text", "")
                        streamed_parts.append(chunk_text)
                        if not _is_json_output(args):
                            sys.stdout.write(chunk_text)
                            sys.stdout.flush()
                    elif current_event == "final":
                        final_payload = json.loads(raw_data)
                        if args.verbose and not _is_json_output(args):
                            print("\n\n--- final ---")
                            print(json.dumps(final_payload, ensure_ascii=True, indent=2))
                    elif current_event == "heartbeat":
                        heartbeat_count += 1
                        if args.verbose and not _is_json_output(args):
                            heartbeat_payload = json.loads(raw_data)
                            stage = heartbeat_payload.get("stage", "running")
                            console.print(f"[dim]heartbeat #{heartbeat_count} stage={stage}[/dim]")
                    elif current_event == "progress":
                        progress_count += 1
                        progress_payload = json.loads(raw_data)
                        stage = str(progress_payload.get("stage", "progress"))
                        message = str(progress_payload.get("message", "")).strip()
                        metadata = progress_payload.get("metadata") or {}
                        extra = ""
                        context_mode = metadata.get("context_mode")
                        if context_mode:
                            extra = f" | mode={context_mode}"
                        if not _is_json_output(args) and (args.verbose or stage == "memory_effect_detected"):
                            console.print(f"[cyan][progress {progress_count}][/cyan] {stage}: {message}{extra}")
                    elif current_event == "done":
                        break
            
            # If no chunks received, fall back to chat (model doesn't support streaming)
            if chunk_count == 0:
                if not _is_json_output(args):
                    console.print("[yellow]Stream empty; falling back to chat mode.[/yellow]")
                return cmd_chat(
                    args,
                    requested_command="stream",
                    fallback_reason="empty_stream",
                )

        _save_session_id(args.session_id)

    except Exception as e:
        if not _is_json_output(args):
            console.print(f"[red]Stream failed ({e}); falling back to chat mode.[/red]")
        return cmd_chat(
            args,
            requested_command="stream",
            fallback_reason=f"stream_error: {e}",
        )
    
    if _is_json_output(args):
        data = dict(final_payload or {})
        data.setdefault("response", "".join(streamed_parts))
        _emit_json(
            _machine_payload(
                data,
                args=args,
                requested_command="stream",
                transport="stream",
            )
        )
        return _validation_exit_code(data, bool(getattr(args, "fail_on_validation", False)))

    print("")
    if STREAM_MARKDOWN_RENDER:
        response_text = "".join(streamed_parts).strip()
        if not response_text and final_payload:
            response_text = str(final_payload.get("response", "")).strip()
        if response_text:
            _print_assistant_response(response_text)
    if final_payload:
        _print_validation_debug(final_payload)
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    params = {
        "session_id": args.session_id,
        "limit": args.limit,
        "include_tool_messages": str(args.include_tool_messages).lower(),
    }
    if args.run_id:
        params["run_id"] = args.run_id

    with _client(args.base_url, args.timeout) as client:
        response = client.get("/history", params=params)
        response.raise_for_status()
        data = response.json()

    items = data.get("items", [])
    if _is_json_output(args):
        result = dict(data)
        result.setdefault("session_id", args.session_id)
        result["schema_version"] = "liara.cli.v1"
        result["ok"] = True
        _emit_json(result)
        return EXIT_OK

    if not items:
        console.print("[dim](no history)[/dim]")
        return 0

    table = Table(title="Session History", show_header=True, header_style="bold cyan")
    table.add_column("Role", style="dim", width=12)
    table.add_column("Content", style="white", width=60)
    
    for item in items:
        role = item.get("role", "unknown").capitalize()
        content = item.get("content", "")[:100]  # Truncate long content
        role_style = "bold green" if role == "User" else "bold magenta"
        table.add_row(Text(role, style=role_style), content)
    
    console.print(table)
    return 0


def cmd_session(args: argparse.Namespace) -> int:
    params = {"session_id": args.session_id, "user_id": args.user_id}
    with _client(args.base_url, args.timeout) as client:
        response = client.get("/session", params=params)
        response.raise_for_status()
        data = response.json()

    if _is_json_output(args):
        result = dict(data)
        result.setdefault("session_id", args.session_id)
        result["schema_version"] = "liara.cli.v1"
        result["ok"] = True
        _emit_json(result)
        return EXIT_OK

    console.print(Panel(
        RichJSON.from_data(data),
        title="[bold yellow]Session Info[/bold yellow]",
        border_style="yellow"
    ))
    return 0


def cmd_help() -> int:
    """Show all available REPL commands."""
    help_text = """
[bold cyan]LIARA CLI Commands[/bold cyan]

[bold]Chat & Response:[/bold]
  <message>          Send message to LLM (respects /mode setting)
  /mode chat|stream  Switch between chat and streaming modes

[bold]Information & Debug:[/bold]
  /history           Show session history (last 20 messages)
  /context           Show context that will be sent to LLM (conversation history)
  /session           Show current session info
    /status            Show current REPL settings (session, mode, max tokens)
  /help              Show this help message
  /health            Check API health & connection status
  /tools             List available tools
  /tools <name>      Show details for a specific tool
  /diag              Diagnose Ollama and available models
  /holo [mode] [sec] Run LIARA holo animation (modes: core/wire/face/scan)

[bold]Direct Tool Access:[/bold]
    /sys <command> [args...]  Invoke the canonical sys tool endpoint

[bold]Session:[/bold]
    /max-tokens <n>    Set max tokens for subsequent prompts
  /quit              Exit REPL

[bold]Multi-line Input:[/bold]
  \"\"\"              Enter paste/multi-line mode (end block with \"\"\" alone)
  /paste             Alias for multi-line mode
"""
    console.print(Panel(help_text, title="[bold green]Help[/bold green]", border_style="green"))
    return 0


def cmd_status(base_url: str, session_id: str, user_id: str, mode: str, max_tokens: int) -> int:
    """Show current REPL runtime settings."""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Key", style="bold dim", min_width=14)
    table.add_column("Value", style="white")
    table.add_row("Base URL", base_url)
    table.add_row("Session", session_id)
    table.add_row("User", user_id)
    table.add_row("Mode", mode)
    table.add_row("Max Tokens", str(max_tokens))
    console.print(Panel(table, title="[bold cyan]REPL Status[/bold cyan]", border_style="cyan"))
    return 0


def cmd_show_context(base_url: str, timeout: float, session_id: str) -> int:
    """Show the conversation context that will be injected into the LLM prompt."""
    params = {
        "session_id": session_id,
        "limit": 100,
        "include_tool_messages": "false",
    }
    try:
        with _client(base_url, timeout) as client:
            response = client.get("/history", params=params)
            response.raise_for_status()
            data = response.json()

        items = data.get("items", [])
        if not items:
            console.print("[dim](no context available yet)[/dim]")
            return 0

        # Build what the LLM will see (Skip current message if it's in there)
        context_lines = []
        for item in items:
            role = item.get("role", "?")
            content = item.get("content", "")[:140]
            context_lines.append(f"[{role}] {content}")

        context_text = "\n".join(context_lines[-8:])  # Show last 8 messages
        console.print(Panel(
            context_text if context_text else "[dim](empty)[/dim]",
            title="[bold cyan]Conversation Context[/bold cyan]",
            border_style="cyan"
        ))
        console.print(f"[dim]({len(items)} messages total in session)[/dim]")
    except Exception as e:
        console.print(f"[red]Failed to load context: {e}[/red]")
        return 1
    return 0


def cmd_tools() -> int:
    """List available tools."""
    table = Table(title="Available Public Tools", show_header=True, header_style="bold cyan")
    table.add_column("Tool", style="dim", width=15)
    table.add_column("Description", style="white")
    
    tools_info = [
        ("sys", "Canonical public gateway for system, filesystem, fetch, and compute actions"),
        ("orientation", "Describe LIARA's capabilities and operating model"),
        ("plot_chart", "Render charts from structured plotting inputs"),
    ]
    
    for name, desc in tools_info:
        table.add_row(Text(name, style="bold green"), desc)
    
    console.print(table)
    return 0


def cmd_tool_info(tool_name: str, base_url: str, timeout: float) -> int:
    """Show details for a specific tool."""
    try:
        with _client(base_url, timeout) as client:
            response = client.get(f"/tools/{tool_name}")
            response.raise_for_status()
            data = response.json()
    except:
        console.print(f"[red]Tool not found: {tool_name}[/red]")
        return 1
    
    console.print(Panel(
        RichJSON.from_data(data),
        title=f"[bold cyan]Tool: {tool_name}[/bold cyan]",
        border_style="cyan"
    ))
    return 0


def cmd_health(args: argparse.Namespace) -> int:
    """Check API health with live backend probe."""
    try:
        with _client(args.base_url, args.timeout) as client:
            health_resp = client.get("/health")
            health_resp.raise_for_status()
            data = health_resp.json()

            live: dict = {}
            try:
                backends_resp = client.get("/health/backends")
                backends_resp.raise_for_status()
                live = backends_resp.json().get("backend_health", {})
            except Exception:
                pass  # live probes optional — fall back to configured booleans

        configured: dict = data.get("backends_configured", {})

        if _is_json_output(args):
            result = dict(data)
            result["backend_health"] = live
            result["schema_version"] = "liara.cli.v1"
            result["ok"] = str(data.get("status") or "").lower() in {"ok", "healthy"}
            _emit_json(result)
            return EXIT_OK if result["ok"] else EXIT_BLOCKED

        # --- service overview ---
        info = Table(show_header=False, box=None, padding=(0, 1))
        info.add_column("Key", style="bold dim", min_width=16)
        info.add_column("Value", style="white")
        info.add_row("Service", data.get("service", "unknown"))
        info.add_row("Status", f"[bold green]{data.get('status', 'unknown')}[/bold green]")
        info.add_row("Memory Mode", data.get("memory_mode", "unknown"))
        console.print(Panel(info, title="[bold green]API Health[/bold green]", border_style="green"))

        # --- backends table ---
        if configured:
            bt = Table(show_header=True, header_style="bold dim", box=None, padding=(0, 1))
            bt.add_column("Backend", style="white", min_width=12)
            bt.add_column("Configured", justify="center", min_width=11)
            bt.add_column("Live Status", justify="left", min_width=14)

            _status_style = {
                "healthy":     "[bold green]OK healthy[/bold green]",
                "degraded":    "[yellow]WARN degraded[/yellow]",
                "unavailable": "[dim]-- unavailable[/dim]",
            }

            for name, active in configured.items():
                cfg_icon = "[green]yes[/green]" if active else "[dim]no[/dim]"
                raw_live = live.get(name)
                live_str = _status_style.get(raw_live, "[dim]--[/dim]") if raw_live else "[dim]--[/dim]"
                bt.add_row(name, cfg_icon, live_str)

            console.print(Panel(bt, title="[bold cyan]Backends[/bold cyan]", border_style="cyan"))

    except Exception as e:
        return _emit_error(
            args,
            error_type="transport_error",
            message=f"API unavailable: {e}",
            exit_code=EXIT_TRANSPORT,
        )
    return EXIT_OK


def cmd_diag() -> int:
    """Diagnose Ollama and model streaming capabilities."""
    import httpx
    
    console.print(Panel("[bold cyan]🔍 Diagnosis[/bold cyan]", border_style="cyan"))
    
    # Check Ollama
    try:
        with httpx.Client(timeout=5) as client:
            response = client.get("http://127.0.0.1:11434/api/tags")
            response.raise_for_status()
            data = response.json()
        
        models = data.get("models", [])
        if not models:
            console.print("[yellow]⚠ No models installed in Ollama[/yellow]")
            return 0
        
        table = Table(title="Available Ollama Models", show_header=True, header_style="bold cyan")
        table.add_column("Model", style="white")
        table.add_column("Size", style="dim")
        table.add_column("streaming", style="yellow")  # All Ollama models support streaming in theory
        
        for model in models:
            name = model.get("name", "unknown")
            size_bytes = model.get("size", 0)
            size_gb = size_bytes / (1024**3)
            table.add_row(name, f"{size_gb:.2f} GB", "✓")
        
        console.print(table)
        console.print("[green]✓ Ollama running[/green]")
        
    except Exception as e:
        console.print(f"[red]✗ Ollama unavailable:{e}[/red]")
        return 1
    
    return 0


def cmd_sys(
    command: str,
    args: list[str] | None,
    base_url: str,
    timeout: float,
    *,
    session_id: str | None = None,
    context: str = "cli.sys",
    source: str = "cli",
) -> int:
    """Invoke the public sys tool endpoint with structured command+args payload."""
    request_id = f"cli-sys-{uuid.uuid4().hex[:12]}"
    payload = {
        "parameters": {
            "command": command,
            "args": list(args or []),
            "request_id": request_id,
            "run_id": request_id,
            "session_id": session_id,
            "source": source,
            "context": context,
        }
    }
    try:
        with _client(base_url, timeout) as client:
            response = client.post("/tools/sys/invoke", json=payload)
            response.raise_for_status()
            data = response.json()

        output = data.get("output", {})
        if isinstance(output, dict):
            stdout = output.get("stdout", "")
        elif isinstance(output, str):
            stdout = output
        else:
            stdout = ""
        if stdout:
            console.print(Panel(stdout, title="[bold cyan]sys[/bold cyan]", border_style="cyan"))
        else:
            console.print(Panel("[dim](empty response)[/dim]", title="[bold cyan]sys[/bold cyan]", border_style="cyan"))
    except Exception as exc:
        console.print(f"[red]sys failed: {exc}[/red]")
        return 1
    return 0


def cmd_holo(duration_seconds: float = 5.0, mode: str = "core") -> int:
    """Render holo animation with configurable visual mode."""
    return run_holo_live(console=console, duration_seconds=duration_seconds, mode=mode)


def _known_repl_commands() -> list[str]:
    """Return canonical slash-commands handled locally by the REPL."""
    return [
        "/help",
        "/status",
        "/health",
        "/history",
        "/context",
        "/session",
        "/mode",
        "/max-tokens",
        "/tools",
        "/diag",
        "/holo",
        "/sys",
        "/paste",
        "/quit",
    ]


def _print_unknown_command_error(message: str) -> None:
    """Show deterministic command error and optional nearest command suggestion."""
    command = message.split(" ", 1)[0].strip().lower()
    suggestion = get_close_matches(command, _known_repl_commands(), n=1, cutoff=0.6)

    console.print(f"[red]command_error:[/red] Unknown command '{command}'.")
    if suggestion:
        console.print(f"[yellow]Did you mean {suggestion[0]} ?[/yellow]")


def _read_input() -> str:
    """Read one message from the user, with optional multi-line mode.

    Type  \"\"\"  alone on a line (or /paste) to enter multi-line mode.
    End the block by typing  \"\"\"  alone again on a new line.
    """
    line = Prompt.ask("[bold cyan]liara[/bold cyan]").strip()
    if line not in ('"""', "/paste"):
        return line
    console.print('[dim]Multi-line mode — paste your text, then type [bold]\"\"\"[/bold] alone on a new line to send[/dim]')
    collected: list[str] = []
    while True:
        try:
            part = input("")
        except EOFError:
            break
        if part.strip() == '"""':
            break
        collected.append(part)
    return "\n".join(collected)


def cmd_repl(args: argparse.Namespace) -> int:
    _save_session_id(args.session_id)
    _repl_startup_preflight(args.base_url, args.timeout)
    console.print(Panel(
        f"Liara CLI REPL\n[dim]Session:[/dim] [cyan]{args.session_id}[/cyan]\n[dim]Mode:[/dim] [yellow]{args.mode}[/yellow]  [dim]Max Tokens:[/dim] [magenta]{args.max_tokens}[/magenta]\n[dim]Commands:[/dim] /help, /status, /health, /history, /context, /session, /mode, /max-tokens, /tools, /diag, /holo, /sys, /quit\n[dim]Multi-line:[/dim] type [bold]\"\"\"[/bold] or [bold]/paste[/bold] to enter paste mode",
        title="[bold green]🚀 Liara[/bold green]",
        border_style="green"
    ))

    mode = args.mode
    while True:
        try:
            message = _read_input()
        except (KeyboardInterrupt, EOFError):
            console.print("[dim]bye[/dim]")
            return 0

        if not message:
            continue

        if message == "/help":
            cmd_help()
            continue
        if message == "/tools":
            cmd_tools()
            continue
        if message.startswith("/tools "):
            tool_name = message.split(" ", 1)[1].strip()
            cmd_tool_info(tool_name, args.base_url, args.timeout)
            continue
        if message in ("/health"):
            cmd_health(args)
            continue

        if message == "/quit":
            console.print("[dim]bye[/dim]")
            return 0
        if message == "/history":
            history_args = argparse.Namespace(
                base_url=args.base_url,
                timeout=args.timeout,
                session_id=args.session_id,
                limit=20,
                include_tool_messages=True,
                run_id=None,
            )
            cmd_history(history_args)
            continue
        if message == "/session":
            session_args = argparse.Namespace(
                base_url=args.base_url,
                timeout=args.timeout,
                session_id=args.session_id,
                user_id=args.user_id,
            )
            cmd_session(session_args)
            continue

        if message == "/status":
            cmd_status(args.base_url, args.session_id, args.user_id, mode, args.max_tokens)
            continue

        if message == "/context":
            cmd_show_context(args.base_url, args.timeout, args.session_id)
            continue

        if message.startswith("/mode "):
            requested = message.split(" ", 1)[1].strip().lower()
            if requested in {"chat", "stream"}:
                mode = requested
                console.print(f"[yellow]mode → {mode}[/yellow]")
            else:
                console.print("[red]unknown mode; use chat or stream[/red]")
            continue

        if message.startswith("/max-tokens "):
            raw_value = message.split(" ", 1)[1].strip()
            try:
                new_value = int(raw_value)
            except ValueError:
                console.print("[red]invalid max token value[/red]")
                continue

            if new_value < 256:
                console.print("[red]max tokens must be at least 256[/red]")
                continue

            args.max_tokens = new_value
            console.print(f"[yellow]max_tokens → {args.max_tokens}[/yellow]")
            continue

        if message == "/sys":
            console.print("[red]usage: /sys <command> [args...] [/red]")
            continue
        if message.startswith("/sys "):
            raw = message.split("/sys ", 1)[1].strip()
            if not raw:
                console.print("[red]usage: /sys <command> [args...] [/red]")
                continue
            parts = raw.split()
            command = parts[0]
            cmd_sys(
                command,
                parts[1:],
                args.base_url,
                args.timeout,
                session_id=args.session_id,
                context="cli.repl.sys",
                source="cli",
            )
            continue
        if message == "/diag":
            cmd_diag()
            continue
        if message.startswith("/holo"):
            holo_mode = "core"
            holo_duration = 5.0
            parts = message.split()
            if len(parts) >= 2:
                token = parts[1].lower()
                if token in VALID_HOLO_MODES:
                    holo_mode = token
                    if len(parts) >= 3:
                        try:
                            holo_duration = float(parts[2])
                        except ValueError:
                            console.print("[red]usage: /holo [mode] [seconds][/red]")
                            continue
                else:
                    try:
                        holo_duration = float(token)
                    except ValueError:
                        console.print("[red]usage: /holo [mode] [seconds][/red]")
                        continue
            cmd_holo(holo_duration, holo_mode)
            continue

        if message.startswith("/"):
            _print_unknown_command_error(message)
            continue

        common = argparse.Namespace(
            base_url=args.base_url,
            timeout=args.timeout,
            session_id=args.session_id,
            user_id=args.user_id,
            max_tokens=args.max_tokens,
            message=message,
            verbose=args.verbose,
        )
        if mode == "stream":
            cmd_stream(common)
        else:
            cmd_chat(common)


def cmd_chat_ui(args: argparse.Namespace) -> int:
    """Launch the modular Textual chat UI."""
    settings = ChatSettings(
        base_url=args.base_url,
        timeout=args.timeout,
        session_id=args.session_id,
        user_id=args.user_id,
        max_tokens=args.max_tokens,
        mode=args.mode,
        verbose=args.verbose,
    )
    return run_textual_chat(settings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CLI for liara-api")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="liara-api base URL")
    parser.add_argument("--timeout", type=float, default=DEFAULT_HTTP_TIMEOUT, help="HTTP timeout in seconds")
    parser.add_argument(
        "--output",
        choices=["human", "json"],
        default=os.getenv("LIARA_CLI_OUTPUT", "human"),
        help="Output contract (global option; place before the subcommand)",
    )
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI/Rich colors")
    parser.add_argument(
        "--fail-on-validation",
        action="store_true",
        help="Return non-zero when the validator warns, revises, or blocks",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    chat = sub.add_parser("chat", help="Send one chat message")
    chat.add_argument("message", help="User prompt")
    chat.add_argument("--session-id", default=_default_session_id())
    chat.add_argument("--user-id", default=DEFAULT_USER_ID)
    chat.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    chat.add_argument("--preferred-provider", default=None)
    chat.add_argument("--preferred-model", default=None)
    chat.add_argument("--verbose", action="store_true")
    chat.set_defaults(func=cmd_chat)

    stream = sub.add_parser("stream", help="Send one streamed chat message via SSE")
    stream.add_argument("message", help="User prompt")
    stream.add_argument("--session-id", default=_default_session_id())
    stream.add_argument("--user-id", default=DEFAULT_USER_ID)
    stream.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    stream.add_argument("--preferred-provider", default=None)
    stream.add_argument("--preferred-model", default=None)
    stream.add_argument("--verbose", action="store_true")
    stream.set_defaults(func=cmd_stream)

    history = sub.add_parser("history", help="Fetch session history")
    history.add_argument("--session-id", required=True)
    history.add_argument("--run-id")
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--include-tool-messages", action="store_true", default=True)
    history.set_defaults(func=cmd_history)

    session = sub.add_parser("session", help="Fetch session snapshot")
    session.add_argument("--session-id", required=True)
    session.add_argument("--user-id", default=DEFAULT_USER_ID)
    session.set_defaults(func=cmd_session)

    repl = sub.add_parser("repl", help="Interactive terminal mode")
    repl.add_argument("--session-id", default=_default_session_id())
    repl.add_argument("--user-id", default=DEFAULT_USER_ID)
    repl.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    repl.add_argument("--verbose", action="store_true")
    repl.add_argument("--mode", choices=["chat", "stream"], default="stream")
    repl.set_defaults(func=cmd_repl)

    chat_ui = sub.add_parser("chat-ui", help="Professional Textual chat interface")
    chat_ui.add_argument("--session-id", default=_default_session_id())
    chat_ui.add_argument("--user-id", default=DEFAULT_USER_ID)
    chat_ui.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    chat_ui.add_argument("--verbose", action="store_true")
    chat_ui.add_argument("--mode", choices=["chat", "stream"], default="stream")
    chat_ui.set_defaults(func=cmd_chat_ui)

    sub.add_parser("health", help="Show API health and configured backends").set_defaults(func=cmd_health)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.no_color:
        console.no_color = True
    if _is_json_output(args) and args.command in {"repl", "chat-ui"}:
        return _emit_error(
            args,
            error_type="usage_error",
            message=f"--output json is not supported for interactive command '{args.command}'",
            exit_code=EXIT_USAGE,
        )

    try:
        return int(args.func(args) or 0)
    except httpx.HTTPStatusError as exc:
        return _emit_error(
            args,
            error_type="http_error",
            message=f"HTTP error: {exc.response.status_code} {exc.response.text}",
            exit_code=EXIT_TRANSPORT,
        )
    except httpx.HTTPError as exc:
        return _emit_error(
            args,
            error_type="transport_error",
            message=f"HTTP client error: {exc}",
            exit_code=EXIT_TRANSPORT,
        )
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
