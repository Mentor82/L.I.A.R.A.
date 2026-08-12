"""Unit tests for services.cli.main."""

from __future__ import annotations

import json
import io

import pytest

from services.cli import main as cli


class _FakeResponse:
    def __init__(self, payload=None, lines=None):
        self._payload = payload or {}
        self._lines = lines or []
        self.headers = {"content-type": "text/event-stream"}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload

    @property
    def text(self):
        return json.dumps(self._payload)

    def iter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamContext:
    def __init__(self, response):
        self.response = response

    def __enter__(self):
        return self.response

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeClient:
    def __init__(self, *, post_payload=None, get_payload=None, stream_lines=None):
        self.post_payload = post_payload or {}
        self.get_payload = get_payload or {}
        self.stream_lines = stream_lines or []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, path, json=None):
        del path, json
        return _FakeResponse(payload=self.post_payload)

    def get(self, path, params=None):
        del path, params
        return _FakeResponse(payload=self.get_payload)

    def stream(self, method, path, json=None):
        del method, path, json
        return _FakeStreamContext(_FakeResponse(lines=self.stream_lines))


class _RaisingClient:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, path, json=None):
        del path, json
        raise RuntimeError("timed out")

    def get(self, path, params=None):
        del path, params
        raise RuntimeError("connect error")


def test_parser_has_expected_commands():
    parser = cli.build_parser()
    args = parser.parse_args(["chat", "hello"])
    assert args.command == "chat"

    args = parser.parse_args(["stream", "hello"])
    assert args.command == "stream"

    args = parser.parse_args(["repl"])
    assert args.command == "repl"


def test_cmd_chat_prints_response(monkeypatch, capsys):
    fake = _FakeClient(
        post_payload={
            "run_id": "run-1",
            "response": "antwort",
            "llm_provider": "mock",
            "llm_model": "mock-model",
            "tools_used": ["current_time"],
        }
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    args = cli.build_parser().parse_args(["chat", "hi"])
    exit_code = cli.main([
        "chat",
        "hi",
        "--session-id",
        "session-a",
        "--user-id",
        "user-a",
    ])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "antwort" in out


def test_cmd_stream_emits_chunk_text(monkeypatch, capsys):
    fake = _FakeClient(
        stream_lines=[
            "event: progress",
            'data: {"stage":"accepted","message":"Chat request accepted","metadata":{}}',
            "event: heartbeat",
            'data: {"ts":"2026-04-15T12:00:00Z","stage":"orchestration_started"}',
            "event: chunk",
            'data: {"run_id":"run-1","index":0,"text":"Hello "}',
            "event: chunk",
            'data: {"run_id":"run-1","index":1,"text":"World"}',
            "event: done",
            "data: {}",
        ]
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    exit_code = cli.main([
        "stream",
        "hi",
        "--session-id",
        "session-a",
        "--user-id",
        "user-a",
    ])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Hello World" in out


def test_cmd_stream_prints_memory_effect_progress(monkeypatch, capsys):
    fake = _FakeClient(
        stream_lines=[
            "event: progress",
            'data: {"stage":"memory_effect_detected","message":"Earlier session context influenced this answer","metadata":{"context_mode":"MEMORY"}}',
            "event: chunk",
            'data: {"run_id":"run-1","index":0,"text":"Antwort"}',
            "event: done",
            "data: {}",
        ]
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    exit_code = cli.main([
        "stream",
        "hi",
        "--session-id",
        "session-a",
        "--user-id",
        "user-a",
    ])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "memory_effect_detected" in out
    assert "mode=MEMORY" in out


def test_cmd_stream_falls_back_to_chat_when_empty(monkeypatch, capsys):
    """Stream with no chunks falls back to cmd_chat."""
    fake = _FakeClient(
        post_payload={
            "run_id": "run-1",
            "response": "fallback antwort from chat",
            "llm_provider": "mock",
            "llm_model": "mock-model",
        },
        stream_lines=[
            "event: done",
            "data: {}",
        ]
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    exit_code = cli.main([
        "stream",
        "hi",
        "--session-id",
        "session-a",
        "--user-id",
        "user-a",
    ])

    out = capsys.readouterr().out
    assert exit_code == 0
    # When stream is empty, falls back to cmd_chat which outputs the response
    assert "fallback antwort from chat" in out


def test_cmd_holo_runs_with_fake_live(monkeypatch):
    monkeypatch.setattr(cli, "run_holo_live", lambda console, duration_seconds, mode: 0)
    exit_code = cli.cmd_holo(0.6, "wire")
    assert exit_code == 0


def test_cmd_chat_handles_http_failure_without_throwing(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: _RaisingClient())

    args = cli.build_parser().parse_args(["chat", "hi"])
    exit_code = cli.cmd_chat(args)

    captured = capsys.readouterr()
    assert exit_code == cli.EXIT_TRANSPORT
    assert captured.out == ""
    assert "Chat request failed" in captured.err


def test_cmd_health_dispatch_accepts_parser_namespace(monkeypatch, capsys):
    fake = _FakeClient(
        get_payload={
            "status": "ok",
            "service": "liara-api",
            "memory_mode": "service",
            "backends_configured": {},
        }
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    exit_code = cli.main(["health"])

    assert exit_code == cli.EXIT_OK
    assert "API Health" in capsys.readouterr().out


def test_health_json_is_single_machine_readable_document(monkeypatch, capsys):
    fake = _FakeClient(
        get_payload={
            "status": "ok",
            "service": "liara-api",
            "memory_mode": "service",
            "backends_configured": {},
        }
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    exit_code = cli.main(["--output", "json", "health"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == cli.EXIT_OK
    assert captured.err == ""
    assert payload["schema_version"] == "liara.cli.v1"
    assert payload["ok"] is True
    assert payload["service"] == "liara-api"
    assert "\x1b[" not in captured.out


def test_json_output_is_safe_for_legacy_windows_code_pages():
    payload = {"formula": "α + β", "message": "Grüße"}
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252")

    cli._emit_json(payload, stream=stream)
    stream.flush()
    rendered = buffer.getvalue().decode("cp1252")

    assert json.loads(rendered) == payload


def test_chat_json_preserves_api_payload_and_trace(monkeypatch, capsys):
    fake = _FakeClient(
        post_payload={
            "run_id": "run-json",
            "response": "antwort",
            "llm_provider": "mock",
            "llm_model": "mock-model",
            "tools_used": ["sys"],
            "validation_passed": True,
            "metadata": {
                "execution_trace": [{"step": "complete"}],
                "validation": {"decision": "accept", "issues": []},
            },
        }
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    exit_code = cli.main([
        "--output",
        "json",
        "chat",
        "hi",
        "--session-id",
        "session-json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == cli.EXIT_OK
    assert captured.err == ""
    assert payload["session_id"] == "session-json"
    assert payload["run_id"] == "run-json"
    assert payload["llm_provider"] == "mock"
    assert payload["metadata"]["execution_trace"] == [{"step": "complete"}]
    assert payload["_cli"]["transport"] == "chat"
    assert payload["_cli"]["fallback"] is False


@pytest.mark.parametrize(
    ("decision", "expected"),
    [("warn", cli.EXIT_VALIDATION), ("revise", cli.EXIT_VALIDATION), ("block", cli.EXIT_BLOCKED)],
)
def test_fail_on_validation_maps_decisions_to_exit_codes(
    monkeypatch,
    capsys,
    decision,
    expected,
):
    fake = _FakeClient(
        post_payload={
            "run_id": "run-validation",
            "response": "result",
            "validation_passed": decision == "accept",
            "metadata": {"validation": {"decision": decision}},
        }
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    exit_code = cli.main([
        "--output",
        "json",
        "--fail-on-validation",
        "chat",
        "hi",
    ])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == expected
    assert payload["ok"] is False


def test_chat_json_transport_error_is_only_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: _RaisingClient())

    exit_code = cli.main(["--output", "json", "chat", "hi"])

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert exit_code == cli.EXIT_TRANSPORT
    assert captured.out == ""
    assert payload["ok"] is False
    assert payload["error"]["type"] == "transport_error"


def test_stream_json_aggregates_chunks_without_mixed_stdout(monkeypatch, capsys):
    fake = _FakeClient(
        stream_lines=[
            "event: progress",
            'data: {"stage":"accepted","message":"accepted","metadata":{}}',
            "event: chunk",
            'data: {"run_id":"run-stream","index":0,"text":"Hello "}',
            "event: chunk",
            'data: {"run_id":"run-stream","index":1,"text":"World"}',
            "event: final",
            'data: {"run_id":"run-stream","response":"Hello World","validation_passed":true,"metadata":{"validation":{"decision":"accept"}}}',
            "event: done",
            "data: {}",
        ]
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    exit_code = cli.main(["--output", "json", "stream", "hi"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == cli.EXIT_OK
    assert captured.err == ""
    assert payload["response"] == "Hello World"
    assert payload["_cli"]["transport"] == "stream"
    assert payload["_cli"]["fallback"] is False


def test_stream_json_marks_chat_fallback(monkeypatch, capsys):
    fake = _FakeClient(
        post_payload={
            "run_id": "run-fallback",
            "response": "fallback",
            "validation_passed": True,
            "metadata": {"validation": {"decision": "accept"}},
        },
        stream_lines=["event: done", "data: {}"],
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    exit_code = cli.main(["--output", "json", "stream", "hi"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == cli.EXIT_OK
    assert payload["_cli"]["requested_command"] == "stream"
    assert payload["_cli"]["transport"] == "chat"
    assert payload["_cli"]["fallback"] is True
    assert payload["_cli"]["fallback_reason"] == "empty_stream"


def test_cmd_sys_invokes_sys_tool_with_command_payload(monkeypatch, capsys):
    captured = {}

    class _FakeSysClient(_FakeClient):
        def post(self, path, json=None):
            captured["path"] = path
            captured["payload"] = json
            return _FakeResponse(payload={"output": {"stdout": "ok", "exit_code": 0}})

    fake = _FakeSysClient()
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)
    monkeypatch.setattr(cli.uuid, "uuid4", lambda: type("_FakeUuid", (), {"hex": "abcdef1234567890"})())

    exit_code = cli.cmd_sys(
        "echo",
        ["hello"],
        "http://127.0.0.1:8010",
        30,
        session_id="session-test",
        context="cli.test.sys",
        source="cli-test",
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert captured["path"] == "/tools/sys/invoke"
    assert captured["payload"]["parameters"]["command"] == "echo"
    assert captured["payload"]["parameters"]["args"] == ["hello"]
    assert captured["payload"]["parameters"]["request_id"] == "cli-sys-abcdef123456"
    assert captured["payload"]["parameters"]["run_id"] == "cli-sys-abcdef123456"
    assert captured["payload"]["parameters"]["session_id"] == "session-test"
    assert captured["payload"]["parameters"]["source"] == "cli-test"
    assert captured["payload"]["parameters"]["context"] == "cli.test.sys"
    assert "ok" in out


def test_cmd_sys_accepts_string_output_payload(monkeypatch, capsys):
    class _FakeSysClient(_FakeClient):
        def post(self, path, json=None):
            return _FakeResponse(payload={"output": "plain text output"})

    fake = _FakeSysClient()
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    exit_code = cli.cmd_sys(
        "date",
        [],
        "http://127.0.0.1:8010",
        30,
        session_id="session-test",
        context="cli.test.sys",
        source="cli-test",
    )

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "plain text output" in out


def test_cmd_tools_lists_current_public_tools(capsys):
    exit_code = cli.cmd_tools()

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "sys" in out
    assert "orientation" in out
    assert "plot_chart" in out
    assert "current_time" not in out
    assert "session_context" not in out


def test_cmd_chat_uses_markdown_render_helper(monkeypatch):
    fake = _FakeClient(
        post_payload={
            "run_id": "run-1",
            "response": "# Titel\n\n- item",
            "llm_provider": "mock",
            "llm_model": "mock-model",
            "tools_used": [],
        }
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    captured = {"text": None}

    def _fake_render(text):
        captured["text"] = text

    monkeypatch.setattr(cli, "_print_assistant_response", _fake_render)

    args = cli.build_parser().parse_args(["chat", "hi"])
    exit_code = cli.cmd_chat(args)

    assert exit_code == 0
    assert captured["text"] == "# Titel\n\n- item"


def test_cmd_stream_renders_markdown_after_stream(monkeypatch):
    fake = _FakeClient(
        stream_lines=[
            "event: chunk",
            'data: {"run_id":"run-1","index":0,"text":"# Titel\\n"}',
            "event: chunk",
            'data: {"run_id":"run-1","index":1,"text":"\\n- item"}',
            "event: done",
            "data: {}",
        ]
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)
    monkeypatch.setattr(cli, "STREAM_MARKDOWN_RENDER", True)

    captured = {"text": None}

    def _fake_render(text):
        captured["text"] = text

    monkeypatch.setattr(cli, "_print_assistant_response", _fake_render)

    exit_code = cli.main([
        "stream",
        "hi",
        "--session-id",
        "session-a",
        "--user-id",
        "user-a",
    ])

    assert exit_code == 0
    assert captured["text"] == "# Titel\n\n- item"


def test_print_validation_debug_renders_mode_ctx_val(capsys):
    payload = {
        "validation_passed": False,
        "metadata": {
            "context_debug": {
                "mode": "CONTEXT",
                "sources": {"chroma": 2, "qdrant": 1, "postgres": 3},
            },
            "validation": {"decision": "warn"},
        },
    }

    cli._print_validation_debug(payload)

    out = capsys.readouterr().out
    assert "[MODE]" in out
    assert "CONTEXT" in out
    assert "[CTX]" in out
    assert "chroma: 2" in out
    assert "qdrant: 1" in out
    assert "postgres: 3" in out
    assert "[VAL]" in out
    assert "warn" in out


def test_cmd_chat_prints_validation_debug_lines(monkeypatch, capsys):
    fake = _FakeClient(
        post_payload={
            "run_id": "run-1",
            "response": "antwort",
            "llm_provider": "mock",
            "llm_model": "mock-model",
            "validation_passed": True,
            "metadata": {
                "context_debug": {
                    "mode": "MEMORY",
                    "sources": {"chroma": 0, "qdrant": 0, "postgres": 2},
                },
                "validation": {"decision": "accept"},
            },
        }
    )
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    args = cli.build_parser().parse_args(["chat", "hi"])
    exit_code = cli.cmd_chat(args)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "[MODE]" in out
    assert "MEMORY" in out
    assert "[VAL]" in out
    assert "accept" in out


def test_uvicorn_hint_from_base_url_uses_host_and_port():
    hint = cli._uvicorn_hint_from_base_url("http://127.0.0.1:8010")
    assert "--host 0.0.0.0" in hint
    assert "--port 8010" in hint


def test_repl_startup_preflight_returns_true_when_api_is_up(monkeypatch):
    fake = _FakeClient(get_payload={"status": "ok"})
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: fake)

    assert cli._repl_startup_preflight("http://127.0.0.1:8010", 30) is True


def test_repl_startup_preflight_prints_hint_when_api_is_down(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_client", lambda _base_url, _timeout: _RaisingClient())

    result = cli._repl_startup_preflight("http://127.0.0.1:8010", 30)

    out = capsys.readouterr().out
    assert result is False
    assert "Startup Check" in out
    assert "python -m uvicorn services.api.app:app" in out


def test_repl_status_runs_locally_without_llm_fallback(monkeypatch):
    monkeypatch.setattr(cli, "_repl_startup_preflight", lambda *_args, **_kwargs: True)

    inputs = iter(["/status", "/quit"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(inputs))

    called = {"status": 0, "chat": 0}

    def _fake_status(*_args, **_kwargs):
        called["status"] += 1
        return 0

    def _fake_chat(*_args, **_kwargs):
        called["chat"] += 1
        return 0

    monkeypatch.setattr(cli, "cmd_status", _fake_status)
    monkeypatch.setattr(cli, "cmd_chat", _fake_chat)

    args = cli.build_parser().parse_args(["repl", "--mode", "chat", "--session-id", "session-test"])
    exit_code = cli.cmd_repl(args)

    assert exit_code == 0
    assert called["status"] == 1
    assert called["chat"] == 0


def test_repl_unknown_slash_command_shows_error_and_skips_llm(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_repl_startup_preflight", lambda *_args, **_kwargs: True)

    inputs = iter(["/satus", "/quit"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(inputs))

    called = {"chat": 0}

    def _fake_chat(*_args, **_kwargs):
        called["chat"] += 1
        return 0

    monkeypatch.setattr(cli, "cmd_chat", _fake_chat)

    args = cli.build_parser().parse_args(["repl", "--mode", "chat", "--session-id", "session-test"])
    exit_code = cli.cmd_repl(args)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert called["chat"] == 0
    assert "command_error" in out
    assert "/status" in out


def test_repl_help_command_executes_locally(monkeypatch):
    monkeypatch.setattr(cli, "_repl_startup_preflight", lambda *_args, **_kwargs: True)

    inputs = iter(["/help", "/quit"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(inputs))

    called = {"help": 0, "chat": 0}

    def _fake_help(*_args, **_kwargs):
        called["help"] += 1
        return 0

    def _fake_chat(*_args, **_kwargs):
        called["chat"] += 1
        return 0

    monkeypatch.setattr(cli, "cmd_help", _fake_help)
    monkeypatch.setattr(cli, "cmd_chat", _fake_chat)

    args = cli.build_parser().parse_args(["repl", "--mode", "chat", "--session-id", "session-test"])
    exit_code = cli.cmd_repl(args)

    assert exit_code == 0
    assert called["help"] == 1
    assert called["chat"] == 0


def test_repl_tools_command_executes_locally(monkeypatch):
    monkeypatch.setattr(cli, "_repl_startup_preflight", lambda *_args, **_kwargs: True)

    inputs = iter(["/tools", "/quit"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(inputs))

    called = {"tools": 0, "chat": 0}

    def _fake_tools(*_args, **_kwargs):
        called["tools"] += 1
        return 0

    def _fake_chat(*_args, **_kwargs):
        called["chat"] += 1
        return 0

    monkeypatch.setattr(cli, "cmd_tools", _fake_tools)
    monkeypatch.setattr(cli, "cmd_chat", _fake_chat)

    args = cli.build_parser().parse_args(["repl", "--mode", "chat", "--session-id", "session-test"])
    exit_code = cli.cmd_repl(args)

    assert exit_code == 0
    assert called["tools"] == 1
    assert called["chat"] == 0


def test_known_repl_commands_exclude_legacy_direct_tool_commands():
    commands = cli._known_repl_commands()

    assert "/sys" in commands
    assert "/time" not in commands
    assert "/search" not in commands
    assert "/fetch" not in commands
    assert "/read" not in commands


def test_repl_sys_command_executes_locally(monkeypatch):
    monkeypatch.setattr(cli, "_repl_startup_preflight", lambda *_args, **_kwargs: True)

    inputs = iter(["/sys echo hello", "/quit"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(inputs))

    called = {"sys": 0, "chat": 0}

    def _fake_sys(*_args, **_kwargs):
        called["sys"] += 1
        called["kwargs"] = _kwargs
        return 0

    def _fake_chat(*_args, **_kwargs):
        called["chat"] += 1
        return 0

    monkeypatch.setattr(cli, "cmd_sys", _fake_sys)
    monkeypatch.setattr(cli, "cmd_chat", _fake_chat)

    args = cli.build_parser().parse_args(["repl", "--mode", "chat", "--session-id", "session-test"])
    exit_code = cli.cmd_repl(args)

    assert exit_code == 0
    assert called["sys"] == 1
    assert called["kwargs"]["session_id"] == "session-test"
    assert called["kwargs"]["context"] == "cli.repl.sys"
    assert called["kwargs"]["source"] == "cli"
    assert called["chat"] == 0


def test_repl_legacy_direct_tool_command_is_unknown(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_repl_startup_preflight", lambda *_args, **_kwargs: True)

    inputs = iter(["/fetch https://example.com", "/quit"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(inputs))

    called = {"chat": 0}

    def _fake_chat(*_args, **_kwargs):
        called["chat"] += 1
        return 0

    monkeypatch.setattr(cli, "cmd_chat", _fake_chat)

    args = cli.build_parser().parse_args(["repl", "--mode", "chat", "--session-id", "session-test"])
    exit_code = cli.cmd_repl(args)

    out = capsys.readouterr().out
    assert exit_code == 0
    assert called["chat"] == 0
    assert "command_error" in out
    assert "/fetch" in out
    assert called["chat"] == 0


def test_repl_history_command_executes_locally(monkeypatch):
    monkeypatch.setattr(cli, "_repl_startup_preflight", lambda *_args, **_kwargs: True)

    inputs = iter(["/history", "/quit"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(inputs))

    called = {"history": 0, "chat": 0}

    def _fake_history(*_args, **_kwargs):
        called["history"] += 1
        return 0

    def _fake_chat(*_args, **_kwargs):
        called["chat"] += 1
        return 0

    monkeypatch.setattr(cli, "cmd_history", _fake_history)
    monkeypatch.setattr(cli, "cmd_chat", _fake_chat)

    args = cli.build_parser().parse_args(["repl", "--mode", "chat", "--session-id", "session-test"])
    exit_code = cli.cmd_repl(args)

    assert exit_code == 0
    assert called["history"] == 1
    assert called["chat"] == 0


def test_repl_quit_command_exits_cleanly(monkeypatch):
    monkeypatch.setattr(cli, "_repl_startup_preflight", lambda *_args, **_kwargs: True)

    inputs = iter(["/quit"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(inputs))

    called = {"chat": 0}

    def _fake_chat(*_args, **_kwargs):
        called["chat"] += 1
        return 0

    monkeypatch.setattr(cli, "cmd_chat", _fake_chat)

    args = cli.build_parser().parse_args(["repl", "--mode", "chat", "--session-id", "session-test"])
    exit_code = cli.cmd_repl(args)

    assert exit_code == 0
    assert called["chat"] == 0


def test_repl_non_slash_input_goes_to_llm(monkeypatch):
    monkeypatch.setattr(cli, "_repl_startup_preflight", lambda *_args, **_kwargs: True)

    inputs = iter(["Hello world", "/quit"])
    monkeypatch.setattr(cli.Prompt, "ask", lambda *_args, **_kwargs: next(inputs))

    called = {"chat": 0}

    def _fake_chat(*_args, **_kwargs):
        called["chat"] += 1
        return 0

    monkeypatch.setattr(cli, "cmd_chat", _fake_chat)

    args = cli.build_parser().parse_args(["repl", "--mode", "chat", "--session-id", "session-test"])
    exit_code = cli.cmd_repl(args)

    assert exit_code == 0
    assert called["chat"] == 1
