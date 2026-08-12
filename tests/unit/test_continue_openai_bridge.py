import pytest
import base64
from fastapi import HTTPException
from fastapi.testclient import TestClient

import scripts.continue_openai_bridge as bridge


def test_bridge_preserves_inline_image_bytes_for_vision():
    raw = b"not-yet-decoded-image-bytes"
    attachment = bridge._attachment_from_content_item({
        "type": "input_image",
        "image_url": f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}",
    })

    assert attachment is not None
    assert base64.b64decode(attachment["content_base64"]) == raw
    assert attachment["media_type"] == "image/png"
    assert attachment["size_bytes"] == len(raw)
    assert attachment["metadata"]["sha256"]


@pytest.mark.asyncio
async def test_call_liara_chat_maps_read_timeout_to_504(monkeypatch):
    class _TimeoutClient:
        def __init__(self, *args, **kwargs):
            del args, kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            del exc_type, exc, tb
            return False

        async def post(self, *args, **kwargs):
            del args, kwargs
            raise bridge.httpx.ReadTimeout("timed out")

    monkeypatch.setattr(bridge.httpx, "AsyncClient", _TimeoutClient)

    with pytest.raises(HTTPException) as exc_info:
        await bridge._call_liara_chat(
            session_id="s1",
            user_id="u1",
            message="hi",
            max_tokens=64,
        )

    assert exc_info.value.status_code == 504
    assert "timeout" in str(exc_info.value.detail).lower()


def test_list_models_returns_all_configured_bridge_ids(monkeypatch):
    monkeypatch.setenv("CONTINUE_BRIDGE_MODEL_IDS", "liara-agent,liara-chat,local")

    with TestClient(bridge.app) as client:
        response = client.get("/v1/models")

    assert response.status_code == 200
    payload = response.json()
    assert [item["id"] for item in payload["data"]] == ["liara-agent", "liara-chat", "local"]


def test_chat_completions_forwards_attachment_payload(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_call_liara_chat(
        *, session_id, user_id, message, max_tokens, attachments=None, available_tools=None, tool_results=None, allow_external_tool_calls=None
    ):
        del available_tools, tool_results, allow_external_tool_calls
        captured["session_id"] = session_id
        captured["user_id"] = user_id
        captured["message"] = message
        captured["attachments"] = attachments or []
        captured["max_tokens"] = max_tokens
        return {"response": "ok"}

    monkeypatch.setattr(bridge, "_call_liara_chat", _fake_call_liara_chat)

    with TestClient(bridge.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "liara-chat",
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Bitte diese Datei analysieren."},
                            {
                                "type": "input_file",
                                "filename": "spec.txt",
                                "media_type": "text/plain",
                                "text": "Alpha\nBeta",
                            },
                        ],
                    }
                ],
                "stream": False,
                "user": "bridge-test-user",
            },
        )

    assert response.status_code == 200
    assert captured["max_tokens"] == 1024
    assert str(captured["message"]) == "Bitte diese Datei analysieren."
    attachments = captured["attachments"]
    assert isinstance(attachments, list)
    assert attachments[0]["name"] == "spec.txt"
    assert attachments[0]["text_content"] == "Alpha\nBeta"


def test_responses_endpoint_forwards_attachment_payload(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_call_liara_chat(
        *, session_id, user_id, message, max_tokens, attachments=None, available_tools=None, tool_results=None, allow_external_tool_calls=None
    ):
        del available_tools, tool_results, allow_external_tool_calls
        captured["session_id"] = session_id
        captured["user_id"] = user_id
        captured["message"] = message
        captured["attachments"] = attachments or []
        captured["max_tokens"] = max_tokens
        return {"response": "ok"}

    monkeypatch.setattr(bridge, "_call_liara_chat", _fake_call_liara_chat)

    with TestClient(bridge.app) as client:
        response = client.post(
            "/v1/responses",
            json={
                "model": "liara-agent",
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": "Nutze die Datei fuer den Plan."},
                            {
                                "type": "input_file",
                                "filename": "plan.txt",
                                "media_type": "text/plain",
                                "text": "Schritt 1\nSchritt 2",
                            },
                        ],
                    }
                ],
                "user": "bridge-responses-user",
            },
        )

    assert response.status_code == 200
    assert captured["user_id"] == "bridge-responses-user"
    assert str(captured["message"]) == "Nutze die Datei fuer den Plan."
    attachments = captured["attachments"]
    assert isinstance(attachments, list)
    assert attachments[0]["name"] == "plan.txt"
    assert attachments[0]["text_content"] == "Schritt 1\nSchritt 2"


def test_chat_completions_emits_bridge_audit(monkeypatch):
    captured_audit: list[dict[str, object]] = []

    async def _fake_call_liara_chat(
        *, session_id, user_id, message, max_tokens, attachments=None, available_tools=None, tool_results=None, allow_external_tool_calls=None
    ):
        del session_id, user_id, message, max_tokens, attachments, available_tools, tool_results, allow_external_tool_calls
        return {"response": "ok"}

    def _fake_emit_bridge_audit(*, event: str, payload: dict[str, object]):
        captured_audit.append({"event": event, "payload": payload})

    monkeypatch.setattr(bridge, "_call_liara_chat", _fake_call_liara_chat)
    monkeypatch.setattr(bridge, "_emit_bridge_audit", _fake_emit_bridge_audit)

    with TestClient(bridge.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "liara-chat",
                "messages": [{"role": "user", "content": "Bitte pruefe 2+2."}],
                "stream": False,
                "user": "audit-test-user",
            },
        )

    assert response.status_code == 200
    assert len(captured_audit) == 2
    assert captured_audit[0]["event"] == "request_parsed"
    first_payload = captured_audit[0]["payload"]
    assert first_payload["endpoint"] == "/v1/chat/completions"
    assert first_payload["query_length"] > 0
    assert first_payload["query_sha256"]
    assert captured_audit[1]["event"] == "response_ready"


def test_responses_emits_bridge_audit(monkeypatch):
    captured_audit: list[dict[str, object]] = []

    async def _fake_call_liara_chat(
        *, session_id, user_id, message, max_tokens, attachments=None, available_tools=None, tool_results=None, allow_external_tool_calls=None
    ):
        del session_id, user_id, message, max_tokens, attachments, available_tools, tool_results, allow_external_tool_calls
        return {"response": "ok"}

    def _fake_emit_bridge_audit(*, event: str, payload: dict[str, object]):
        captured_audit.append({"event": event, "payload": payload})

    monkeypatch.setattr(bridge, "_call_liara_chat", _fake_call_liara_chat)
    monkeypatch.setattr(bridge, "_emit_bridge_audit", _fake_emit_bridge_audit)

    with TestClient(bridge.app) as client:
        response = client.post(
            "/v1/responses",
            json={
                "model": "liara-agent",
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Bitte pruefe 3*3."}],
                    }
                ],
                "user": "audit-test-user",
            },
        )

    assert response.status_code == 200
    assert len(captured_audit) == 2
    assert captured_audit[0]["event"] == "request_parsed"
    first_payload = captured_audit[0]["payload"]
    assert first_payload["endpoint"] == "/v1/responses"
    assert first_payload["query_length"] > 0
    assert first_payload["query_sha256"]
    assert captured_audit[1]["event"] == "response_ready"


def test_chat_completions_trims_oversized_query(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_call_liara_chat(
        *, session_id, user_id, message, max_tokens, attachments=None, available_tools=None, tool_results=None, allow_external_tool_calls=None
    ):
        del session_id, user_id, max_tokens, attachments, available_tools, tool_results, allow_external_tool_calls
        captured["message"] = message
        return {"response": "ok"}

    monkeypatch.setattr(bridge, "_call_liara_chat", _fake_call_liara_chat)
    monkeypatch.setattr(bridge, "CONTINUE_BRIDGE_MAX_QUERY_CHARS", 200)

    huge_text = "X" * 2500
    with TestClient(bridge.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "liara-chat",
                "messages": [
                    {"role": "system", "content": "Systemanweisung"},
                    {"role": "user", "content": huge_text},
                ],
            },
        )

    assert response.status_code == 200
    assert isinstance(captured.get("message"), str)
    assert len(str(captured["message"])) <= 200


def test_chat_completions_includes_system_role_when_enabled(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_call_liara_chat(
        *, session_id, user_id, message, max_tokens, attachments=None, available_tools=None, tool_results=None, allow_external_tool_calls=None
    ):
        del session_id, user_id, max_tokens, attachments, available_tools, tool_results, allow_external_tool_calls
        captured["message"] = message
        return {"response": "ok"}

    monkeypatch.setattr(bridge, "_call_liara_chat", _fake_call_liara_chat)
    monkeypatch.setattr(bridge, "CONTINUE_BRIDGE_INCLUDE_SYSTEM_ROLE", True)

    with TestClient(bridge.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "liara-chat",
                "messages": [
                    {"role": "system", "content": "Antworte im Stil eines Architekten."},
                    {"role": "user", "content": "Erklaere CQRS kurz."},
                ],
            },
        )

    assert response.status_code == 200
    forwarded = str(captured.get("message") or "")
    assert "Systemanweisungen (aus Client):" in forwarded
    assert "Antworte im Stil eines Architekten." in forwarded
    assert "Aktuelle Nutzeranfrage:" in forwarded
    assert "Erklaere CQRS kurz." in forwarded


def test_chat_completions_forwards_system_only_when_enabled(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_call_liara_chat(
        *, session_id, user_id, message, max_tokens, attachments=None, available_tools=None, tool_results=None, allow_external_tool_calls=None
    ):
        del session_id, user_id, max_tokens, attachments, available_tools, tool_results, allow_external_tool_calls
        captured["message"] = message
        return {"response": "ok"}

    monkeypatch.setattr(bridge, "_call_liara_chat", _fake_call_liara_chat)
    monkeypatch.setattr(bridge, "CONTINUE_BRIDGE_INCLUDE_SYSTEM_ROLE", True)

    with TestClient(bridge.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "liara-chat",
                "messages": [
                    {"role": "system", "content": "Halte Antworten unter 5 Saetzen."},
                ],
            },
        )

    assert response.status_code == 200
    forwarded = str(captured.get("message") or "")
    assert forwarded.startswith("Systemanweisungen (aus Client):")
    assert "Halte Antworten unter 5 Saetzen." in forwarded


def test_chat_completions_falls_back_when_response_empty(monkeypatch):
    async def _fake_call_liara_chat(
        *, session_id, user_id, message, max_tokens, attachments=None, available_tools=None, tool_results=None, allow_external_tool_calls=None
    ):
        del session_id, user_id, message, max_tokens, attachments, available_tools, tool_results, allow_external_tool_calls
        return {
            "response": "",
            "metadata": {
                "validation": {
                    "decision": "revise",
                    "issues": ["Antwort war leer"],
                }
            },
        }

    monkeypatch.setattr(bridge, "_call_liara_chat", _fake_call_liara_chat)

    with TestClient(bridge.app) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "liara-chat",
                "messages": [{"role": "user", "content": "Teste Fallback"}],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    assert isinstance(content, str)
    assert "keine Antwort" in content


def test_messages_to_query_payload_strips_placeholder_user_context_block():
    messages = [
        {
            "role": "user",
            "content": (
                "USER_CONTEXT:\n"
                "source: current_file\n"
                "path: ...\n"
                "language: ...\n"
                "content: ...\n\n"
                "Bitte analysiere die Datei kurz."
            ),
        }
    ]

    query, attachments = bridge._messages_to_query_payload(messages)

    assert attachments == []
    assert "USER_CONTEXT:" not in query
    assert "Bitte analysiere die Datei kurz." in query


def test_messages_to_query_payload_keeps_non_placeholder_user_context_block():
    messages = [
        {
            "role": "user",
            "content": (
                "USER_CONTEXT:\n"
                "source: current_file\n"
                "path: frontend/gtk-ui/src/liara_api.c\n"
                "language: c\n"
                "content: static void dispatch_stream_progress(...)\n\n"
                "Bitte auf Race Conditions schauen."
            ),
        }
    ]

    query, attachments = bridge._messages_to_query_payload(messages)

    assert attachments == []
    assert "USER_CONTEXT:" in query
    assert "frontend/gtk-ui/src/liara_api.c" in query
    assert "Bitte auf Race Conditions schauen." in query


def test_responses_forwards_tools_and_tool_results(monkeypatch):
    captured: dict[str, object] = {}

    async def _fake_call_liara_chat(
        *, session_id, user_id, message, max_tokens, attachments=None, available_tools=None, tool_results=None, allow_external_tool_calls=None
    ):
        del session_id, user_id, message, max_tokens, attachments, allow_external_tool_calls
        captured["available_tools"] = available_tools
        captured["tool_results"] = tool_results
        return {"response": "ok"}

    monkeypatch.setattr(bridge, "_call_liara_chat", _fake_call_liara_chat)

    with TestClient(bridge.app) as client:
        response = client.post(
            "/v1/responses",
            json={
                "model": "liara-agent",
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Bitte Plane erstellen."}],
                    },
                    {
                        "type": "function_call_output",
                        "call_id": "call_list_dir",
                        "name": "list_dir",
                        "output": "{\"items\": [\"a\", \"b\"]}",
                    },
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "list_dir",
                            "description": "List directory",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
            },
        )

    assert response.status_code == 200
    available_tools = captured.get("available_tools")
    assert isinstance(available_tools, list)
    assert available_tools[0]["function"]["name"] == "list_dir"
    tool_results = captured.get("tool_results")
    assert isinstance(tool_results, list)
    assert tool_results[0]["tool_call_id"] == "call_list_dir"
    assert tool_results[0]["name"] == "list_dir"
    assert "items" in str(tool_results[0]["content"])


def test_responses_returns_function_call_output_when_liara_requests_tools(monkeypatch):
    async def _fake_call_liara_chat(
        *, session_id, user_id, message, max_tokens, attachments=None, available_tools=None, tool_results=None, allow_external_tool_calls=None
    ):
        del session_id, user_id, message, max_tokens, attachments, available_tools, tool_results, allow_external_tool_calls
        return {
            "response": "",
            "pending_tool_calls": [
                {
                    "id": "call_abc123",
                    "function": {
                        "name": "search_files",
                        "arguments": {"query": "continue bridge"},
                    },
                }
            ],
        }

    monkeypatch.setattr(bridge, "_call_liara_chat", _fake_call_liara_chat)

    with TestClient(bridge.app) as client:
        response = client.post(
            "/v1/responses",
            json={
                "model": "liara-agent",
                "input": [{"role": "user", "content": "Bitte nutze Tools."}],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "response"
    assert payload["output"]
    first = payload["output"][0]
    assert first["type"] == "function_call"
    assert first["name"] == "search_files"
    assert first["call_id"] == "call_abc123"
