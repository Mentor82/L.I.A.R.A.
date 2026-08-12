from __future__ import annotations

import sys
from pathlib import Path

import pytest


TEX_UI_ROOT = Path(__file__).resolve().parents[2] / "frontend" / "tex-ui"
if str(TEX_UI_ROOT) not in sys.path:
    sys.path.insert(0, str(TEX_UI_ROOT))

from textual_chat.app import _guard_unverified_write_claim
from textual_chat.client import _chat_payload
from textual_chat.commands import parse_sys_invocation
from textual_chat.models import ChatSettings


def _settings() -> ChatSettings:
    return ChatSettings(
        base_url="http://127.0.0.1:8010",
        timeout=90,
        session_id="session-ui-test",
        user_id="mirko",
        max_tokens=1536,
        workspace_root=r"\\wsl.localhost\Debian\home\liara",
        sandbox_root="/home/liara/workspace",
    )


def test_chat_payload_binds_request_to_wsl_sandbox() -> None:
    payload = _chat_payload(_settings(), "Erstelle worker.py")
    assert payload["sandbox_root"] == "/home/liara/workspace"
    assert "workspace_root" not in payload


def test_parse_sys_uses_direct_argv_and_multiline_stdin() -> None:
    invocation = parse_sys_invocation(
        "tee /home/liara/workspace/worker.py\nprint('hello')"
    )
    assert invocation.command == "tee"
    assert invocation.args == ["/home/liara/workspace/worker.py"]
    assert invocation.stdin_text == "print('hello')"


def test_parse_sys_inline_stdin_preserves_single_quoted_argument() -> None:
    invocation = parse_sys_invocation(
        'tee /home/liara/workspace/worker.py --stdin "print(\'hello\')"'
    )
    assert invocation.stdin_text == "print('hello')"


def test_unverified_model_write_claim_is_visibly_guarded() -> None:
    text = _guard_unverified_write_claim(
        "Die Dateien wurden angelegt.",
        {"tools_used": [], "tool_outputs": {}, "validation_passed": True},
    )
    assert text.startswith("[UNVERIFIED FILESYSTEM CLAIM]")


def test_verified_write_claim_names_exactly_confirmed_target() -> None:
    original = "Datei geschrieben."
    payload = {
        "tool_outputs": {
            "sys": {
                "kind": "workspace_write",
                "verified": True,
                "target_path": "/home/liara/workspace/worker.py",
            }
        }
    }
    guarded = _guard_unverified_write_claim(original, payload)
    assert guarded.startswith("[VERIFIED FILESYSTEM EVIDENCE]")
    assert "/home/liara/workspace/worker.py" in guarded
    assert guarded.endswith(original)


@pytest.mark.asyncio
async def test_invoke_sys_sends_structured_stdin_and_session_context() -> None:
    from unittest.mock import AsyncMock

    from textual_chat.client import LiaraApiClient

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "success",
                "output": "ok",
                "metadata": {"mutation_verified": True},
            }

    settings = _settings()
    settings.workspace_session_id = "sess-0123456789abcdef"
    client = LiaraApiClient(settings)
    original_client = client._client
    fake_client = AsyncMock()
    fake_client.post = AsyncMock(return_value=_Response())
    client._client = fake_client
    try:
        result = await client.invoke_sys(
            "tee",
            ["/home/liara/workspace/worker.py"],
            stdin_text="print('hello')\n",
        )
    finally:
        await original_client.aclose()

    assert result["metadata"]["mutation_verified"] is True
    request = fake_client.post.call_args.kwargs["json"]
    assert request["parameters"]["command"] == "tee"
    assert request["parameters"]["stdin_text"] == "print('hello')\n"
    assert request["parameters"]["workspace_session_id"] == "sess-0123456789abcdef"
