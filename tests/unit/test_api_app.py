"""Unit tests for liara-api endpoints."""

import asyncio
import base64
import hashlib
import io
import importlib.util
import json
import wave
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from services.api import create_api_app, create_default_memory_adapter
from services.contracts import (
    ChatArtifact,
    MemoryDreamingProposalListResponse,
    MemoryDreamingProposalRecord,
    MemoryDreamingStatusResponse,
    MemoryEvidence,
    MemoryHistoryQueryRequest,
    MemoryLifecycleStatus,
    MemoryServiceStatus,
    OrchestratorResponse,
    ToolExecutionResult,
    TtsDevicePlacement,
    TtsHealthResponse,
)
from services.memory.store import EphemeralMemoryStore, NullMemoryStore
from services.memory.tier_store import MemoryLayer
from services.memory_adapter import InProcessMemoryAdapter
from services.config import Settings
from services.tools.governance import create_pending_sys_governance_proposal
from services.api.app import _cors_allowed_origins
from services.inference.tts_adapter import TtsAdapterError


@pytest.fixture(autouse=True)
def _default_local_sandbox_mode(monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")


def test_default_cors_origins_cover_parallel_frontends(monkeypatch):
    monkeypatch.delenv("LIARA_API_CORS_ALLOW_ORIGINS", raising=False)

    origins = _cors_allowed_origins()

    assert "http://127.0.0.1:3001" in origins
    assert "http://127.0.0.1:3002" in origins
    assert "http://localhost:3002" in origins


class FakeOrchestrator:
    """Deterministic orchestrator stub for API endpoint tests."""

    async def run(self, request):
        return OrchestratorResponse(
            run_id=request.run_id,
            final_response=f"echo: {request.query}",
            tools_executed=["current_time"],
            tool_results={"current_time": "2026-04-14T12:00:00Z"},
            state_final="complete",
            llm_generation={
                "provider": "mock",
                "model": "mock-model",
                "ttft_ms": 10.0,
                "gen_ms": 20.0,
                "context_debug": {
                    "mode": "MEMORY",
                    "sources": {"chroma": 0, "qdrant": 0, "postgres": 2},
                },
            },
            validation_result={
                "passed": True,
                "decision": "accept",
                "checks": {"fast_check": "pass"},
                "issues": [],
                "confidence_score": 0.99,
                "suggestions": None,
                "retry_count": 0,
            },
            execution_trace=[],
        )


class SlowFakeOrchestrator(FakeOrchestrator):
    """Slow orchestrator stub to force heartbeat emission during streaming."""

    async def run(self, request):
        await asyncio.sleep(0.25)
        return await super().run(request)


class CapturingFakeOrchestrator(FakeOrchestrator):
    """Stub that keeps the last request for contract assertions."""

    def __init__(self):
        self.last_request = None

    async def run(self, request):
        self.last_request = request
        return await super().run(request)


class ArtifactFakeOrchestrator(FakeOrchestrator):
    """Stub that returns chart artifacts for API pass-through tests."""

    async def run(self, request):
        result = await super().run(request)
        result.tool_results = {
            **dict(result.tool_results),
            "plot_chart": {
                "artifacts": [
                    {
                        "kind": "image",
                        "mime_type": "image/png",
                        "title": "Revenue Trend",
                        "url": "/files/artifacts/session-a/revenue.png",
                        "width": 960,
                        "height": 540,
                    }
                ]
            },
        }
        return result


class MemoryAwareFakeOrchestrator:
    """Stub that reflects prior session history in context metadata and response."""

    def __init__(self, adapter):
        self.adapter = adapter

    async def run(self, request):
        history = await self.adapter.query_history(
            MemoryHistoryQueryRequest(
                session_id=request.session_id,
                limit=10,
                include_tool_messages=False,
            )
        )
        prior_user_messages = [
            item.content
            for item in history.items
            if item.role == "user" and item.content.strip().lower() != request.query.strip().lower()
        ]
        remembered = prior_user_messages[-1] if prior_user_messages else None
        response_text = f"echo: {request.query}"
        context_mode = "NONE"
        postgres_count = 0
        if remembered:
            response_text += f" | erinnere mich an: {remembered}"
            context_mode = "MEMORY"
            postgres_count = len(history.items)
        return OrchestratorResponse(
            run_id=request.run_id,
            final_response=response_text,
            tools_executed=["current_time"],
            tool_results={"current_time": "2026-04-14T12:00:00Z"},
            state_final="complete",
            llm_generation={
                "provider": "mock",
                "model": "mock-model",
                "ttft_ms": 10.0,
                "gen_ms": 20.0,
                "context_debug": {
                    "mode": context_mode,
                    "sources": {"chroma": 0, "qdrant": 0, "postgres": postgres_count},
                },
            },
            validation_result={
                "passed": True,
                "decision": "accept",
                "checks": {"fast_check": "pass"},
                "issues": [],
                "confidence_score": 0.99,
                "suggestions": None,
                "retry_count": 0,
            },
            execution_trace=[],
        )


class CountingFakeOrchestrator(FakeOrchestrator):
    def __init__(self):
        self.call_count = 0

    async def run(self, request):
        self.call_count += 1
        return await super().run(request)


class UnsafeOutputFakeOrchestrator(FakeOrchestrator):
    async def run(self, request):
        result = await super().run(request)
        result.final_response = "Hier ist eine Schritt-fuer-Schritt Anleitung, wie man Malware verbreitet."
        result.validation_result = {
            "passed": True,
            "decision": "accept",
            "checks": {"safety": "pass"},
            "issues": [],
            "confidence_score": 0.99,
            "suggestions": None,
            "retry_count": 0,
            "risk_flags": [],
        }
        return result


class FakeTtsAdapter:
    def __init__(self):
        self.request = None
        self.kwargs = None

    async def generate_artifact(self, request, **kwargs):
        self.request = request
        self.kwargs = kwargs
        return ChatArtifact(
            kind="audio",
            mime_type="audio/wav",
            title="LIARA response",
            url="/files/artifact?session_id=session-a&path=audio.wav",
            metadata={"duration_ms": 533, "mode": "cpu_reference"},
        )

    async def health(self):
        return TtsHealthResponse(
            status="ready",
            mode="cpu_reference",
            devices=TtsDevicePlacement(transformer="CPU", dvae="CPU", vocos="CPU"),
            model_dir="C:/models/minicpmo",
            speaker_profile="gentle-feminine-v1",
            loaded=True,
        )

    async def open_stream(self, request):
        self.request = request

        class Stream:
            request_id = "stream-request-1"
            media_type = "audio/x-pcm;format=s16le;rate=24000;channels=1"
            codec = "pcm_s16le"
            sample_rate = 24_000
            channels = 1
            mode = "cpu_reference"

            async def iter_bytes(self):
                yield b"\x01\x00" * 128

            async def aclose(self):
                return None

        return Stream()


class FailingTtsAdapter(FakeTtsAdapter):
    def __init__(self, error):
        super().__init__()
        self.error = error

    async def health(self):
        raise self.error

    async def generate_artifact(self, request, **kwargs):
        raise self.error

    async def open_stream(self, request):
        raise self.error


def test_create_default_memory_adapter_falls_back_when_redis_dependency_missing(monkeypatch):
    monkeypatch.setattr(Settings, "MEMORY_MODE", "postgres")
    monkeypatch.setattr(Settings, "REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(Settings, "POSTGRES_URL", None)
    monkeypatch.setattr(Settings, "QDRANT_URL", None)

    real_find_spec = importlib.util.find_spec

    def _fake_find_spec(name: str, *args, **kwargs):
        if name == "redis":
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec)

    adapter = create_default_memory_adapter()

    assert isinstance(adapter, InProcessMemoryAdapter)
    assert isinstance(adapter.memory_layer.session_store, EphemeralMemoryStore)


@pytest.mark.asyncio
async def test_speech_generate_returns_session_scoped_audio_artifact():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    tts_adapter = FakeTtsAdapter()
    app = create_api_app(
        orchestrator=FakeOrchestrator(),
        memory_adapter=adapter,
        tts_adapter=tts_adapter,
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/speech/generate",
            json={
                "session_id": "session-a",
                "text": "Eine sanfte Testantwort.",
                "sandbox_root": "frontend",
                "speaker_profile": "gentle-feminine-v1",
                "max_audio_tokens": 125,
                "seed": 7,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["kind"] == "audio"
    assert payload["mime_type"] == "audio/wav"
    assert payload["content_base64"] is None
    assert payload["url"].startswith("/files/artifact?")
    assert tts_adapter.request.text == "Eine sanfte Testantwort."
    assert tts_adapter.request.speaker_profile == "gentle-feminine-v1"
    assert tts_adapter.request.max_audio_tokens == 125
    assert tts_adapter.request.seed == 7
    assert tts_adapter.kwargs["session_id"] == "session-a"
    assert Path(tts_adapter.kwargs["sandbox_root"]).resolve() == Path("frontend").resolve()


@pytest.mark.asyncio
async def test_speech_health_returns_internal_tts_state():
    tts_adapter = FakeTtsAdapter()
    app = create_api_app(orchestrator=FakeOrchestrator(), tts_adapter=tts_adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/speech/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["speaker_profile"] == "gentle-feminine-v1"


@pytest.mark.asyncio
async def test_speech_stream_proxies_binary_pcm_contract():
    tts_adapter = FakeTtsAdapter()
    app = create_api_app(orchestrator=FakeOrchestrator(), tts_adapter=tts_adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/speech/stream",
            json={
                "session_id": "session-a",
                "text": "Eine sanfte Streamantwort.",
                "speaker_profile": "gentle-feminine-v1",
                "max_audio_tokens": 125,
                "seed": 7,
                "codec": "pcm_s16le",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/x-pcm")
    assert response.headers["x-liara-tts-stream-contract"] == "audio_stream/v1"
    assert response.headers["x-liara-tts-codec"] == "pcm_s16le"
    assert response.content == b"\x01\x00" * 128
    assert tts_adapter.request.text == "Eine sanfte Streamantwort."


@pytest.mark.asyncio
async def test_speech_stream_defaults_to_webm_opus():
    tts_adapter = FakeTtsAdapter()
    app = create_api_app(orchestrator=FakeOrchestrator(), tts_adapter=tts_adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/speech/stream",
            json={
                "session_id": "session-a",
                "text": "Eine Opus-Testantwort.",
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("audio/webm")
    assert response.headers["x-liara-tts-codec"] == "webm_opus"
    assert response.headers["x-liara-tts-sample-rate"] == "48000"
    assert response.content.startswith(bytes.fromhex("1a45dfa3"))


@pytest.mark.asyncio
async def test_speech_stream_optionally_commits_pcm_as_downloadable_wav(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")
    monkeypatch.setenv("LIARA_READ_ROOT", str(tmp_path))
    tts_adapter = FakeTtsAdapter()
    app = create_api_app(orchestrator=FakeOrchestrator(), tts_adapter=tts_adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/speech/stream",
            json={
                "session_id": "session-a",
                "text": "Eine persistente Testantwort.",
                "codec": "pcm_s16le",
                "persist_artifact": True,
            },
        )
        artifact_url = response.headers["x-liara-tts-artifact-url"]
        artifact = await client.get(artifact_url)

    assert response.status_code == 200
    assert response.content == b"\x01\x00" * 128
    assert response.headers["x-liara-tts-artifact-commit"] == "on-complete"
    assert artifact.status_code == 200
    with wave.open(io.BytesIO(artifact.content), "rb") as wav:
        assert wav.getnchannels() == 1
        assert wav.getframerate() == 24_000
        assert wav.getnframes() == 128


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "error", "expected_status", "expected_code"),
    [
        ("/speech/health", TtsAdapterError("tts_unavailable", "TTS service is unavailable", status_code=503, retryable=True), 503, "tts_unavailable"),
        ("/speech/generate", TtsAdapterError("tts_timeout", "TTS service request timed out", status_code=504, retryable=True), 504, "tts_timeout"),
        ("/speech/generate", TtsAdapterError("tts_queue_full", "TTS queue is full", status_code=429, retryable=True), 429, "tts_queue_full"),
        ("/speech/stream", TtsAdapterError("tts_queue_full", "TTS queue is full", status_code=429, retryable=True), 429, "tts_queue_full"),
    ],
)
async def test_speech_endpoints_preserve_tts_errors(path, error, expected_status, expected_code):
    app = create_api_app(
        orchestrator=FakeOrchestrator(),
        tts_adapter=FailingTtsAdapter(error),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        if path.endswith("health"):
            response = await client.get(path)
        else:
            response = await client.post(path, json={"session_id": "session-a", "text": "Hallo"})

    assert response.status_code == expected_status
    assert response.json()["detail"] == {
        "code": expected_code,
        "message": str(error),
        "retryable": True,
    }


@pytest.mark.asyncio
async def test_chat_history_and_session_endpoints_round_trip():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat_response = await client.post(
            "/chat",
            json={
                "session_id": "session-a",
                "user_id": "user-a",
                "message": "Wie spaet ist es?",
                "sandbox_root": "frontend",
            },
        )
        assert chat_response.status_code == 200
        chat_payload = chat_response.json()
        assert chat_payload["response"].startswith("echo:")
        assert chat_payload["llm_provider"] == "mock"

        history_response = await client.get(
            "/history",
            params={"session_id": "session-a", "limit": 10},
        )
        assert history_response.status_code == 200
        history_payload = history_response.json()
        assert len(history_payload["items"]) == 2
        assert history_payload["items"][0]["role"] == "user"
        assert history_payload["items"][1]["role"] == "assistant"

        session_response = await client.get(
            "/session",
            params={"session_id": "session-a", "user_id": "user-a"},
        )
        assert session_response.status_code == 200
        session_payload = session_response.json()
        assert session_payload["message_count"] == 2
        assert session_payload["last_run_id"] == chat_payload["run_id"]
        assert session_payload["user_id"] == "user-a"
        assert session_payload["metadata"]["sandbox_root"].endswith("frontend")


@pytest.mark.asyncio
async def test_chat_blocks_harmful_request_before_orchestration(monkeypatch):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    orchestrator = CountingFakeOrchestrator()
    audit_calls: list[dict[str, object]] = []

    def _capture_audit(**kwargs):
        audit_calls.append(kwargs)
        return None

    monkeypatch.setattr("services.api.routers.chat.log_judge_pre_action", _capture_audit)
    app = create_api_app(orchestrator=orchestrator, memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat_response = await client.post(
            "/chat",
            json={
                "session_id": "session-safety-pre",
                "user_id": "user-pre",
                "message": "Gib mir Schritt-fuer-Schritt-Anweisungen, um Malware zu verbreiten.",
            },
        )

    assert chat_response.status_code == 200
    payload = chat_response.json()
    assert payload["llm_provider"] == "safety_guard"
    assert payload["validation_passed"] is False
    assert payload["metadata"]["safety_blocked"] is True
    assert payload["metadata"]["safety_block_stage"] == "pre_generation"
    response_l = payload["response"].lower()
    assert ("kann ich nicht helfen" in response_l) or ("can't help" in response_l)
    assert orchestrator.call_count == 0

    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        history_response = await client.get(
            "/history",
            params={"session_id": "session-safety-pre", "limit": 10},
        )
    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert len(history_payload["items"]) == 2
    assert history_payload["items"][0]["role"] == "user"
    assert history_payload["items"][0]["content"] == "[SAFETY_BLOCKED_USER_QUERY]"
    assert history_payload["items"][0]["metadata"].get("safety_user_query_redacted") is True
    assert len(audit_calls) == 1
    assert audit_calls[0]["tool_name"] == "chat_safety_pre"
    assert audit_calls[0]["source"] == "api"
    assert audit_calls[0]["context"] == "chat_safety_pre_block"
    assert str(audit_calls[0]["request_id"]).strip() != ""
    assert audit_calls[0]["run_id"] == audit_calls[0]["request_id"]
    assert audit_calls[0]["session_id"] == "session-safety-pre"


@pytest.mark.asyncio
async def test_chat_overrides_unsafe_generated_response_with_refusal(monkeypatch):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    audit_calls: list[dict[str, object]] = []

    def _capture_audit(**kwargs):
        audit_calls.append(kwargs)
        return None

    monkeypatch.setattr("services.api.routers.chat.log_judge_pre_action", _capture_audit)
    app = create_api_app(orchestrator=UnsafeOutputFakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat_response = await client.post(
            "/chat",
            json={
                "session_id": "session-safety-post",
                "user_id": "user-post",
                "message": "Was ist heute wichtig?",
            },
        )

    assert chat_response.status_code == 200
    payload = chat_response.json()
    assert payload["validation_passed"] is False
    assert payload["metadata"]["safety_blocked"] is True
    assert payload["metadata"]["safety_block_stage"] == "post_generation"
    assert payload["metadata"]["validation"]["decision"] == "block"
    assert "policy_safety_violation" in payload["metadata"]["validation"].get("risk_flags", [])
    response_l = payload["response"].lower()
    assert ("kann ich nicht helfen" in response_l) or ("can't help" in response_l)
    assert len(audit_calls) == 1
    assert audit_calls[0]["tool_name"] == "chat_safety_post"
    assert audit_calls[0]["source"] == "api"
    assert audit_calls[0]["context"] == "chat_safety_post_block"
    assert str(audit_calls[0]["request_id"]).strip() != ""
    assert audit_calls[0]["run_id"] == audit_calls[0]["request_id"]
    assert audit_calls[0]["session_id"] == "session-safety-post"


@pytest.mark.asyncio
async def test_chat_attachments_are_included_in_orchestrator_query_and_history_metadata():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    orchestrator = CapturingFakeOrchestrator()
    app = create_api_app(orchestrator=orchestrator, memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat_response = await client.post(
            "/chat",
            json={
                "session_id": "session-files",
                "user_id": "user-files",
                "message": "Bitte pruefe die Datei.",
                "attachments": [
                    {
                        "name": "notes.txt",
                        "media_type": "text/plain",
                        "text_content": "Zeile A\nZeile B",
                        "source": "unit-test",
                    }
                ],
            },
        )

        assert chat_response.status_code == 200
        assert orchestrator.last_request is not None
        assert orchestrator.last_request.attachments[0].name == "notes.txt"
        assert "Bereitgestellte Dateien/Anhänge" in orchestrator.last_request.query
        assert "Zeile A" in orchestrator.last_request.query
        payload = chat_response.json()
        assert payload["metadata"]["attachment_scan_results"][0]["status"] == "clean"

        history_response = await client.get(
            "/history",
            params={"session_id": "session-files", "limit": 10},
        )
        assert history_response.status_code == 200
        history_payload = history_response.json()
        assert history_payload["items"][0]["metadata"]["attachment_count"] == 1
        assert history_payload["items"][0]["metadata"]["attachments"][0]["name"] == "notes.txt"
        assert history_payload["items"][0]["metadata"]["attachments"][0]["scan"]["status"] == "clean"


@pytest.mark.asyncio
async def test_chat_image_is_normalized_for_vision_without_history_payload():
    image_buffer = io.BytesIO()
    Image.new("RGB", (4, 3), color="navy").save(image_buffer, format="PNG")
    raw = image_buffer.getvalue()
    adapter = InProcessMemoryAdapter(MemoryLayer(
        session_store=EphemeralMemoryStore(), fact_store=EphemeralMemoryStore(),
        retrieval_index=EphemeralMemoryStore(), graph_store=NullMemoryStore(),
    ))
    orchestrator = CapturingFakeOrchestrator()
    app = create_api_app(orchestrator=orchestrator, memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/chat", json={
            "session_id": "session-image", "user_id": "user-image",
            "message": "Was ist auf dem Bild?",
            "attachments": [{
                "name": "scene.png", "media_type": "image/png",
                "content_base64": base64.b64encode(raw).decode("ascii"),
            }],
        })
        history = await client.get("/history", params={"session_id": "session-image", "limit": 10})

    assert response.status_code == 200
    attachment = orchestrator.last_request.attachments[0]
    assert base64.b64decode(attachment.content_base64) == raw
    assert attachment.metadata["vision"]["sha256"] == hashlib.sha256(raw).hexdigest()
    history_meta = history.json()["items"][0]["metadata"]["attachments"][0]
    assert "content_base64" not in json.dumps(history_meta)


@pytest.mark.asyncio
async def test_chat_includes_artifacts_from_orchestrator_result():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=ArtifactFakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat_response = await client.post(
            "/chat",
            json={
                "session_id": "session-a",
                "user_id": "user-a",
                "message": "Zeig mir einen Plot.",
            },
        )

    assert chat_response.status_code == 200
    payload = chat_response.json()
    assert isinstance(payload.get("artifacts"), list)
    assert payload["artifacts"][0]["kind"] == "image"
    assert payload["artifacts"][0]["mime_type"] == "image/png"
    assert payload["artifacts"][0]["source_tool"] == "plot_chart"
    assert isinstance(payload.get("metadata"), dict)
    assert isinstance(payload["metadata"].get("validation"), dict)
    assert "execution_trace" in payload["metadata"]
    assert "debug_run" in payload["metadata"]


@pytest.mark.asyncio
async def test_chat_rejects_attachment_with_malware_signature():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat_response = await client.post(
            "/chat",
            json={
                "session_id": "session-malware",
                "user_id": "user-malware",
                "message": "Bitte lies das.",
                "attachments": [
                    {
                        "name": "eicar.txt",
                        "media_type": "text/plain",
                        "text_content": "X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*",
                    }
                ],
            },
        )

    assert chat_response.status_code == 422
    payload = chat_response.json()
    assert payload["detail"]["attachment"] == "eicar.txt"
    assert payload["detail"]["scan"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_file_upload_stores_clean_text_and_returns_attachment_payload(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_READ_ROOT", str(tmp_path))
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        upload_response = await client.post(
            "/files/upload",
            data={
                "session_id": "session-upload",
                "user_id": "user-upload",
                "sandbox_root": ".",
            },
            files={"file": ("report.txt", b"alpha\nbeta", "text/plain")},
        )

    assert upload_response.status_code == 200
    payload = upload_response.json()
    assert payload["scan"]["status"] == "clean"
    assert payload["attachment"]["name"] == "report.txt"
    assert payload["attachment"]["text_content"] == "alpha\nbeta"
    stored_path = payload["attachment"]["metadata"]["stored_path"]
    assert tmp_path.name in stored_path


@pytest.mark.asyncio
async def test_artifact_endpoint_serves_session_scoped_file(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_READ_ROOT", str(tmp_path))
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    artifact_dir = tmp_path / ".liara_artifacts" / "session-a"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifact_dir / "chart.png"
    artifact_bytes = b"\x89PNG\r\n\x1a\nmock"
    artifact_path.write_bytes(artifact_bytes)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/files/artifact",
            params={
                "session_id": "session-a",
                "path": ".liara_artifacts/session-a/chart.png",
            },
        )

    assert response.status_code == 200
    assert response.content == artifact_bytes
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_artifact_endpoint_blocks_cross_session_access(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_READ_ROOT", str(tmp_path))
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    foreign_dir = tmp_path / ".liara_artifacts" / "other-session"
    foreign_dir.mkdir(parents=True, exist_ok=True)
    (foreign_dir / "chart.png").write_bytes(b"mock")

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get(
            "/files/artifact",
            params={
                "session_id": "session-a",
                "path": ".liara_artifacts/other-session/chart.png",
            },
        )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_session_metadata_uses_canonical_wsl_sandbox_root(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "wsl")
    monkeypatch.setenv("LIARA_WSL_DISTRO", "Debian")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_ROOT", "/home/liara/workspace")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_WINDOWS_ROOT", str(tmp_path))

    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/session",
            json={
                "session_id": "session-wsl",
                "user_id": "user-wsl",
                "sandbox_root": "frontend",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["sandbox_root"] == "/home/liara/workspace/frontend"
    assert payload["metadata"]["sandbox_root_mode"] == "wsl"
    assert payload["metadata"]["sandbox_root_distro"] == "Debian"
    assert payload["metadata"]["sandbox_root_local"].endswith("frontend")


@pytest.mark.asyncio
async def test_file_upload_uses_canonical_wsl_paths_in_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "wsl")
    monkeypatch.setenv("LIARA_WSL_DISTRO", "Debian")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_ROOT", "/home/liara/workspace")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_WINDOWS_ROOT", str(tmp_path))

    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        upload_response = await client.post(
            "/files/upload",
            data={
                "session_id": "session-upload-wsl",
                "user_id": "user-upload-wsl",
                "sandbox_root": "frontend",
            },
            files={"file": ("report.txt", b"alpha\nbeta", "text/plain")},
        )

    assert upload_response.status_code == 200
    payload = upload_response.json()
    metadata = payload["attachment"]["metadata"]
    assert metadata["sandbox_root"] == "/home/liara/workspace/frontend"
    assert metadata["sandbox_root_local"].endswith("frontend")
    assert metadata["stored_path"].startswith("/home/liara/workspace/frontend/.liara_uploads/session-upload-wsl/")
    assert metadata["stored_path_local"].endswith("report.txt")


@pytest.mark.asyncio
async def test_file_upload_blocks_malware_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_READ_ROOT", str(tmp_path))
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        upload_response = await client.post(
            "/files/upload",
            data={
                "session_id": "session-upload-malware",
                "user_id": "user-upload-malware",
                "sandbox_root": ".",
            },
            files={
                "file": (
                    "eicar.txt",
                    b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*",
                    "text/plain",
                )
            },
        )

    assert upload_response.status_code == 422
    payload = upload_response.json()
    assert payload["detail"]["scan"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_session_upsert_persists_sandbox_root_and_sys_invoke_uses_it(monkeypatch, tmp_path):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "wsl")
    monkeypatch.setenv("LIARA_WSL_DISTRO", "Debian")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_ROOT", "/home/liara/workspace")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_WINDOWS_ROOT", str(tmp_path))

    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    captured_parameters: list[dict[str, object]] = []

    async def _fake_execute_tool(self, request):
        captured_parameters.append(dict(request.parameters))
        return ToolExecutionResult(
            tool_name=request.tool_name,
            status="success",
            output={"workdir": request.parameters.get("workdir")},
            error=None,
            execution_ms=1.0,
        )

    monkeypatch.setattr("services.tools.coordinator.ToolCoordinator.execute_tool", _fake_execute_tool)
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        session_response = await client.post(
            "/session",
            json={
                "session_id": "session-files",
                "user_id": "user-files",
                "sandbox_root": "frontend",
            },
        )
        assert session_response.status_code == 200
        assert session_response.json()["metadata"]["sandbox_root"].endswith("frontend")

        invoke_response = await client.post(
            "/tools/sys/invoke",
            json={
                "parameters": {
                    "command": "ls",
                    "args": ["."],
                    "session_id": "session-files",
                }
            },
        )
        assert invoke_response.status_code == 200
        invoke_payload = invoke_response.json()
        assert invoke_payload["status"] == "success"
        assert invoke_payload["output"]["workdir"] == "/home/liara/workspace/frontend"

    assert len(captured_parameters) == 1
    assert captured_parameters[0]["command"] == "ls"
    assert captured_parameters[0]["args"] == ["."]
    assert captured_parameters[0]["workdir"] == "/home/liara/workspace/frontend"


@pytest.mark.asyncio
async def test_chat_rejects_sandbox_root_outside_boundary():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        chat_response = await client.post(
            "/chat",
            json={
                "session_id": "session-bad",
                "user_id": "user-bad",
                "message": "Hallo",
                "sandbox_root": "../../..",
            },
        )

    assert chat_response.status_code == 400


@pytest.mark.asyncio
async def test_health_and_chat_stream_endpoints():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        health_response = await client.get("/health")
        assert health_response.status_code == 200
        assert "public" in (health_response.headers.get("cache-control") or "")
        assert health_response.headers.get("etag")
        health_payload = health_response.json()
        assert health_payload["status"] == "ok"
        assert health_payload["service"] == "liara-api"

        health_not_modified = await client.get(
            "/health",
            headers={"If-None-Match": health_response.headers["etag"]},
        )
        assert health_not_modified.status_code == 304

        stream_response = await client.post(
            "/chat/stream",
            json={
                "session_id": "session-stream",
                "user_id": "user-stream",
                "message": "stream test",
            },
        )
        assert stream_response.status_code == 200
        assert "no-store" in (stream_response.headers.get("cache-control") or "")
        assert stream_response.headers["content-type"].startswith("text/event-stream")
        body = stream_response.text
        assert "event: progress" in body
        assert "event: chunk" in body
        assert "event: final" in body
        assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_stream_emits_heartbeat_for_slow_requests(monkeypatch):
    monkeypatch.setenv("LIARA_STREAM_HEARTBEAT_SECONDS", "0.1")
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=SlowFakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        stream_response = await client.post(
            "/chat/stream",
            json={
                "session_id": "session-stream-heartbeat",
                "user_id": "user-stream-heartbeat",
                "message": "stream heartbeat test",
            },
        )

    assert stream_response.status_code == 200
    body = stream_response.text
    assert "event: heartbeat" in body
    assert "event: progress" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_chat_stream_emits_artifact_event_and_final_payload_artifacts():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=ArtifactFakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        stream_response = await client.post(
            "/chat/stream",
            json={
                "session_id": "session-artifact-stream",
                "user_id": "user-artifact-stream",
                "message": "plotte bitte",
            },
        )

    assert stream_response.status_code == 200
    body = stream_response.text
    assert "event: artifact" in body
    assert "event: final" in body
    assert '"artifacts":' in body


@pytest.mark.asyncio
async def test_chat_stream_reports_progress_and_memory_effect_across_session_turns():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=MemoryAwareFakeOrchestrator(adapter), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        first_response = await client.post(
            "/chat/stream",
            json={
                "session_id": "session-memory-effect",
                "user_id": "user-memory-effect",
                "message": "Mein Name ist Mira",
            },
        )
        assert first_response.status_code == 200
        first_body = first_response.text
        assert "memory_effect_detected" not in first_body

        second_response = await client.post(
            "/chat/stream",
            json={
                "session_id": "session-memory-effect",
                "user_id": "user-memory-effect",
                "message": "Wie heiße ich?",
            },
        )

    assert second_response.status_code == 200
    second_body = second_response.text
    assert "event: progress" in second_body
    assert "memory_effect_detected" in second_body
    assert '"context_mode": "MEMORY"' in second_body or '"context_mode":"MEMORY"' in second_body
    assert "erinnere mich an: Mein Name ist Mira" in second_body


@pytest.mark.asyncio
async def test_chat_metadata_contains_validation_decision_and_context_debug():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/chat",
            json={
                "session_id": "session-meta",
                "user_id": "user-meta",
                "message": "test metadata",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["validation"]["decision"] == "accept"
    assert payload["metadata"]["context_debug"]["mode"] == "MEMORY"
    assert payload["metadata"]["debug_run"]["api_timings_ms"]["total"] >= 0
    assert payload["metadata"]["debug_run"]["selected_tools"] == ["current_time"]


@pytest.mark.asyncio
async def test_chat_metadata_preserves_hybrid_control_fields_for_tui_consumers():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )

    class PolicyAwareFakeOrchestrator(FakeOrchestrator):
        async def run(self, request):
            response = await super().run(request)
            response.validation_result = {
                "passed": False,
                "decision": "block",
                "checks": {"fast_check": "pass", "safety": "fail"},
                "issues": ["policy guardrail triggered"],
                "confidence_score": 0.33,
                "suggestions": ["do not present unsafe output"],
                "retry_count": 1,
                "judge_post": {
                    "decision": "block",
                    "reason_code": "judge.post.blocked",
                },
                "math_signals": {
                    "control_mode": "hard",
                    "control_mode_after": "hard",
                    "resolution_basis": "policy",
                    "resolved_mode": "hard",
                    "resolved_action": "fallback_safe_response",
                    "trigger_reasons": ["judge_post_block", "score_fach_critical"],
                },
                "decision_context": {
                    "effective": {
                        "control_mode_after": "hard",
                        "resolution_basis": "policy",
                        "resolved_mode": "hard",
                        "resolved_action": "fallback_safe_response",
                    }
                },
            }
            return response

    app = create_api_app(orchestrator=PolicyAwareFakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/chat",
            json={
                "session_id": "session-policy-meta",
                "user_id": "user-policy-meta",
                "message": "test policy metadata",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    validation = payload["metadata"]["validation"]

    assert validation["decision"] == "block"
    assert validation["judge_post"]["decision"] == "block"
    assert validation["math_signals"]["resolution_basis"] == "policy"
    assert validation["math_signals"]["resolved_mode"] == "hard"
    assert validation["math_signals"]["resolved_action"] == "fallback_safe_response"
    assert "judge_post_block" in validation["math_signals"]["trigger_reasons"]
    assert validation["decision_context"]["effective"]["resolved_mode"] == "hard"


@pytest.mark.asyncio
async def test_chat_replaces_graph_priority_violation_with_block_response():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )

    class GraphPriorityFakeOrchestrator(FakeOrchestrator):
        async def run(self, request):
            response = await super().run(request)
            response.final_response = "liara-live-api depends on liara-live-database"
            response.validation_result = {
                "passed": False,
                "decision": "revise",
                "checks": {"fast_check": "pass", "graph_priority": "fail"},
                "issues": [
                    "Severe contradiction: response overrides or omits authoritative graph relation "
                    "liara-live-api -[DEPENDS_ON]-> liara-live-memory"
                ],
                "confidence_score": 0.35,
                "risk_flags": [],
                "suggestions": None,
                "retry_count": 1,
            }
            return response

    app = create_api_app(orchestrator=GraphPriorityFakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/chat",
            json={
                "session_id": "session-graph-priority",
                "user_id": "user-graph-priority",
                "message": "test graph priority",
            },
        )

    assert response.status_code == 200
    payload = response.json()

    assert "liara-live-database" not in payload["response"]
    assert "liara-live-api -[DEPENDS_ON]-> liara-live-memory" in payload["response"]
    assert payload["validation_passed"] is False
    assert payload["metadata"]["validation"]["decision"] == "block"
    assert payload["metadata"]["validation"]["graph_priority_blocked"] is True
    assert "graph_priority_violation" in payload["metadata"]["validation"]["risk_flags"]


@pytest.mark.asyncio
async def test_chat_metadata_contains_prompt_debug_from_execution_trace():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )

    class TraceOrchestrator(FakeOrchestrator):
        async def run(self, request):
            response = await super().run(request)
            response.execution_trace = [
                {
                    "from": "tool_execution",
                    "to": "llm_generation",
                    "timestamp": "2026-04-17T00:00:00+00:00",
                    "reason": "Generating response",
                    "metadata": {
                        "prompt_debug": {
                            "prompt": "[QUERY]\ntest metadata",
                            "chars": 21,
                        },
                        "context_debug": {
                            "mode": "MEMORY",
                            "sources": {"chroma": 0, "qdrant": 0, "postgres": 2},
                        },
                    },
                },
                {
                    "from": "llm_generation",
                    "to": "validation",
                    "timestamp": "2026-04-17T00:00:01+00:00",
                    "reason": "Validating output",
                    "metadata": {
                        "decision": "accept",
                        "issues": [],
                        "timing_ms": 1.5,
                    },
                },
            ]
            return response

    app = create_api_app(orchestrator=TraceOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/chat",
            json={
                "session_id": "session-prompt-debug",
                "user_id": "user-prompt-debug",
                "message": "test metadata",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["debug_run"]["prompt_debug"]["prompt"].startswith("[QUERY]")
    assert payload["metadata"]["debug_run"]["validation_trace"]["decision"] == "accept"


@pytest.mark.asyncio
async def test_chat_metadata_contains_reasoning_metrics_from_completion_trace():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )

    class MetricsTraceOrchestrator(FakeOrchestrator):
        async def run(self, request):
            response = await super().run(request)
            response.execution_trace = [
                {
                    "from": "validation",
                    "to": "complete",
                    "timestamp": "2026-04-21T00:00:01+00:00",
                    "reason": "Success",
                    "metadata": {
                        "timing_ms": 25.0,
                        "reasoning_metrics": {
                            "mode": "advisory",
                            "total_cost": 3.5,
                            "rds_v2": 1.7,
                            "total_risk": 0.6,
                            "utility": -2.5,
                        },
                    },
                }
            ]
            return response

    app = create_api_app(orchestrator=MetricsTraceOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/chat",
            json={
                "session_id": "session-reasoning-metrics",
                "user_id": "user-reasoning-metrics",
                "message": "test reasoning metrics",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["metadata"]["reasoning_metrics"]["mode"] == "advisory"
    assert payload["metadata"]["reasoning_metrics"]["rds_v2"] == 1.7
    assert payload["metadata"]["debug_run"]["reasoning_metrics"]["total_risk"] == 0.6


@pytest.mark.asyncio
async def test_tools_endpoints_list_metadata_and_invoke():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        list_response = await client.get("/tools")
        assert list_response.status_code == 200
        assert "public" in (list_response.headers.get("cache-control") or "")
        assert list_response.headers.get("etag")
        list_payload = list_response.json()
        assert list_payload["status"] == "success"
        assert list_payload["count"] >= 1

        tools_not_modified = await client.get(
            "/tools",
            headers={"If-None-Match": list_response.headers["etag"]},
        )
        assert tools_not_modified.status_code == 304

        metadata_response = await client.get("/tools/sys")
        assert metadata_response.status_code == 200
        assert metadata_response.headers.get("etag")
        metadata_payload = metadata_response.json()
        assert metadata_payload["tool"]["name"] == "sys"

        metadata_not_modified = await client.get(
            "/tools/sys",
            headers={"If-None-Match": metadata_response.headers["etag"]},
        )
        assert metadata_not_modified.status_code == 304

        unknown_response = await client.get("/tools/does-not-exist")
        assert unknown_response.status_code == 404


@pytest.mark.asyncio
async def test_tools_invoke_forwards_simulation_mode(monkeypatch):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )

    captured_simulation_modes: list[bool] = []

    async def _fake_execute_tool(self, request):
        captured_simulation_modes.append(bool(request.simulation_mode))
        return ToolExecutionResult(
            tool_name=request.tool_name,
            status="success",
            output={"simulation_mode": bool(request.simulation_mode)},
            error=None,
            execution_ms=1.0,
        )

    monkeypatch.setattr("services.tools.coordinator.ToolCoordinator.execute_tool", _fake_execute_tool)
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        sim_response = await client.post(
            "/tools/sys/invoke",
            json={
                "parameters": {"command": "date", "context": "agent_time_lookup"},
                "timeout_seconds": 5,
                "simulation_mode": True,
            },
        )
        assert sim_response.status_code == 200
        assert sim_response.json()["output"]["simulation_mode"] is True

        real_response = await client.post(
            "/tools/sys/invoke",
            json={
                "parameters": {"command": "date", "context": "agent_time_lookup"},
                "timeout_seconds": 5,
            },
        )
        assert real_response.status_code == 200
        assert real_response.json()["output"]["simulation_mode"] is False

    assert captured_simulation_modes == [True, False]


@pytest.mark.asyncio
async def test_tools_invoke_adds_default_traceability_metadata(monkeypatch):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )

    captured_parameters: list[dict[str, object]] = []

    async def _fake_execute_tool(self, request):
        captured_parameters.append(dict(request.parameters))
        return ToolExecutionResult(
            tool_name=request.tool_name,
            status="success",
            output={"ok": True},
            error=None,
            execution_ms=1.0,
        )

    monkeypatch.setattr("services.tools.coordinator.ToolCoordinator.execute_tool", _fake_execute_tool)
    monkeypatch.setattr("services.api.routers.tools.uuid4", lambda: type("_FakeUuid", (), {"hex": "1234567890abcdef"})())
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/tools/sys/invoke",
            json={
                "parameters": {"command": "date", "session_id": "session-a"},
                "timeout_seconds": 5,
            },
        )

    assert response.status_code == 200
    assert captured_parameters == [
        {
            "command": "date",
            "session_id": "session-a",
            "request_id": "api-tool-1234567890ab",
            "run_id": "api-tool-1234567890ab",
            "source": "api",
            "context": "api.tools.sys.invoke",
        }
    ]


@pytest.mark.asyncio
async def test_sys_governance_proposal_decision_and_invoke(monkeypatch, tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )

    async def _fake_execute_tool(self, request):
        return ToolExecutionResult(
            tool_name=request.tool_name,
            status="success",
            output={"ok": True, "proposal_id": request.parameters.get("proposal_id")},
            error=None,
            execution_ms=1.0,
        )

    monkeypatch.setattr("services.tools.coordinator.ToolCoordinator.execute_tool", _fake_execute_tool)
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(tmp_path / "sys_governance_test.json"))
    events_path = tmp_path / "sys_governance_events.jsonl"
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(events_path))
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        proposal_resp = await client.post(
            "/tools/sys/governance/proposals",
            json={
                "command": "health",
                "parameters": {"context": "test"},
                "capability": "service_health",
                "rationale": "Need runtime health snapshot",
                "requested_by": "orchestrator",
                "session_id": "sess-gov-1",
            },
        )
        assert proposal_resp.status_code == 200
        proposal_payload = proposal_resp.json()
        proposal_id = proposal_payload["item"]["proposal_id"]
        assert proposal_payload["item"]["decision"] == "pending"
        assert len(proposal_payload["item"]["invocation_digest"]) == 64
        assert proposal_payload["item"]["invocation"] == {
            "state": "not_invoked",
            "attempt_count": 0,
            "success_count": 0,
        }

        decision_resp = await client.post(
            "/tools/sys/governance/decisions",
            json={
                "proposal_id": proposal_id,
                "decision": "approved",
                "decided_by": "human",
                "decision_reason": "approved for diagnostics",
            },
        )
        assert decision_resp.status_code == 200
        assert decision_resp.json()["item"]["decision"] == "approved"
        assert decision_resp.json()["item"]["decision_at"]

        mismatched_resp = await client.post(
            "/tools/sys/invoke",
            json={
                "parameters": {
                    "command": "health",
                    "args": ["unexpected"],
                    "proposal_id": proposal_id,
                },
                "simulation_mode": True,
            },
        )
        assert mismatched_resp.status_code == 409
        assert "does not match" in str(mismatched_resp.json().get("detail"))

        invoke_resp = await client.post(
            "/tools/sys/invoke",
            json={
                "parameters": {
                    "command": "health",
                    "proposal_id": proposal_id,
                },
                "simulation_mode": True,
            },
        )
        assert invoke_resp.status_code == 200
        assert invoke_resp.json()["output"]["proposal_id"] == proposal_id

        reused_resp = await client.post(
            "/tools/sys/invoke",
            json={
                "parameters": {"command": "health", "proposal_id": proposal_id},
                "simulation_mode": True,
            },
        )
        assert reused_resp.status_code == 409
        assert "limit reached" in str(reused_resp.json().get("detail"))

        events_resp = await client.get(
            "/tools/sys/governance/events",
            params={"proposal_id": proposal_id, "limit": 20},
        )
        assert events_resp.status_code == 200
        assert events_resp.headers["cache-control"] == "no-store"
        assert events_resp.json()["total"] == 4
        assert {item["event_type"] for item in events_resp.json()["items"]} == {
            "proposal_created",
            "proposal_decided",
            "invocation_attempted",
            "invocation_completed",
        }

    app_reloaded = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)
    transport_reloaded = httpx.ASGITransport(app=app_reloaded)
    async with httpx.AsyncClient(transport=transport_reloaded, base_url="http://testserver") as client:
        list_resp = await client.get("/tools/sys/governance/proposals")
        assert list_resp.status_code == 200
        listed = list_resp.json()["items"]
        reloaded_proposal = next(item for item in listed if item.get("proposal_id") == proposal_id)
        assert reloaded_proposal["invocation"]["state"] == "completed"
        assert reloaded_proposal["invocation"]["attempt_count"] == 1
        assert reloaded_proposal["invocation"]["success_count"] == 1
        assert reloaded_proposal["audit_reference"]["endpoint"].startswith("/tools/sys/governance/events")
        assert list_resp.json()["summary"]["consumed"] == 1
        assert list_resp.json()["summary"]["invocation_states"]["completed"] == 1

    assert events_path.exists()
    lines = [line for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) >= 4
    events = [json.loads(line) for line in lines]
    assert any(evt.get("event_type") == "proposal_created" and evt.get("proposal_id") == proposal_id for evt in events)
    assert any(evt.get("event_type") == "proposal_decided" and evt.get("proposal_id") == proposal_id for evt in events)
    assert any(evt.get("event_type") == "invocation_attempted" and evt.get("proposal_id") == proposal_id for evt in events)
    assert any(evt.get("event_type") == "invocation_completed" and evt.get("proposal_id") == proposal_id for evt in events)


@pytest.mark.asyncio
async def test_sys_governance_apply_and_rollback_are_bound_and_single_use(monkeypatch, tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    store_path = tmp_path / "sys_governance_apply_rollback.json"
    events_path = tmp_path / "sys_governance_apply_rollback.jsonl"
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(store_path))
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(events_path))
    old_content = "before\n"
    new_content = "after\n"
    old_digest = hashlib.sha256(old_content.encode("utf-8")).hexdigest()
    new_digest = hashlib.sha256(new_content.encode("utf-8")).hexdigest()
    executions = []

    async def _fake_execute_tool(self, request):
        executions.append(dict(request.parameters))
        command = request.parameters.get("command")
        if command == "cat":
            return ToolExecutionResult(
                tool_name="sys",
                status="success",
                output=old_content,
                execution_ms=1.0,
            )
        rollback = request.parameters.get("source") == "governance_rollback"
        digest = old_digest if rollback else new_digest
        return ToolExecutionResult(
            tool_name="sys",
            status="success",
            output=request.parameters.get("stdin_text"),
            execution_ms=1.0,
            metadata={
                "mutation_verified": True,
                "mutation_evidence": {"verified": True, "sha256": digest},
            },
        )

    monkeypatch.setattr("services.tools.coordinator.ToolCoordinator.execute_tool", _fake_execute_tool)
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/tools/sys/governance/proposals",
            json={
                "command": "tee",
                "parameters": {
                    "command": "tee",
                    "args": ["/home/liara/workspace/config.txt"],
                    "stdin_text": new_content,
                    "target_path": "/home/liara/workspace/config.txt",
                    "write_mode": "overwrite",
                    "storage_scope": "wsl_workspace",
                    "workdir": "/home/liara/workspace",
                },
                "capability": "workspace_write",
                "rationale": "verify reversible governance action",
                "requested_by": "test",
            },
        )
        proposal_id = created.json()["item"]["proposal_id"]
        decided = await client.post(
            "/tools/sys/governance/decisions",
            json={
                "proposal_id": proposal_id,
                "decision": "approved",
                "decided_by": "human",
                "decision_reason": "apply the reviewed change",
            },
        )
        applied = await client.post(
            "/tools/sys/governance/actions",
            json={
                "proposal_id": proposal_id,
                "action": "apply",
                "acted_by": "human",
                "action_reason": "execute approved mutation",
            },
        )
        applied_again = await client.post(
            "/tools/sys/governance/actions",
            json={
                "proposal_id": proposal_id,
                "action": "apply",
                "acted_by": "human",
                "action_reason": "must not execute twice",
            },
        )
        rolled_back = await client.post(
            "/tools/sys/governance/actions",
            json={
                "proposal_id": proposal_id,
                "action": "rollback",
                "acted_by": "human",
                "action_reason": "restore the captured state",
            },
        )
        rolled_back_again = await client.post(
            "/tools/sys/governance/actions",
            json={
                "proposal_id": proposal_id,
                "action": "rollback",
                "acted_by": "human",
                "action_reason": "must not compensate twice",
            },
        )
        events = await client.get(
            "/tools/sys/governance/events",
            params={"proposal_id": proposal_id, "limit": 50},
        )

    assert decided.status_code == 200
    assert applied.status_code == 200
    applied_item = applied.json()["item"]
    assert applied_item["transaction"]["state"] == "applied"
    assert applied_item["transaction"]["rollback"]["supported"] is True
    snapshot = applied_item["transaction"]["rollback"]["snapshot"]
    assert snapshot["sha256"] == old_digest
    assert snapshot["size_bytes"] == len(old_content.encode("utf-8"))
    assert applied_again.status_code == 409
    assert rolled_back.status_code == 200
    rollback_payload = rolled_back.json()
    assert rollback_payload["item"]["transaction"]["state"] == "rolled_back"
    assert rollback_payload["item"]["transaction"]["rollback"]["restored_sha256"] == old_digest
    assert rollback_payload["rollback_proposal"]["rollback_of"] == proposal_id
    assert rollback_payload["rollback_proposal"]["invocation"]["attempt_count"] == 1
    assert rolled_back_again.status_code == 409
    assert [item["command"] for item in executions] == ["cat", "tee", "tee"]
    assert executions[-1]["stdin_text"] == old_content
    event_types = [item["event_type"] for item in reversed(events.json()["items"])]
    governance_action_events = [item for item in event_types if item.startswith("governance_")]
    assert governance_action_events == [
        "governance_apply_attempted",
        "governance_apply_completed",
        "governance_rollback_attempted",
        "governance_rollback_completed",
    ]


@pytest.mark.asyncio
async def test_sys_governance_non_reversible_apply_refuses_rollback(monkeypatch, tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(tmp_path / "sys_governance_non_reversible.json"))
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(tmp_path / "sys_governance_non_reversible.jsonl"))

    async def _fake_execute_tool(self, request):
        return ToolExecutionResult(tool_name="sys", status="success", output="ok", execution_ms=1.0)

    monkeypatch.setattr("services.tools.coordinator.ToolCoordinator.execute_tool", _fake_execute_tool)
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        created = await client.post(
            "/tools/sys/governance/proposals",
            json={
                "command": "health",
                "parameters": {"command": "health"},
                "capability": "service_health",
                "rationale": "non-mutating apply",
                "requested_by": "test",
            },
        )
        proposal_id = created.json()["item"]["proposal_id"]
        await client.post(
            "/tools/sys/governance/decisions",
            json={
                "proposal_id": proposal_id,
                "decision": "approved",
                "decided_by": "human",
                "decision_reason": "approved",
            },
        )
        applied = await client.post(
            "/tools/sys/governance/actions",
            json={
                "proposal_id": proposal_id,
                "action": "apply",
                "acted_by": "human",
                "action_reason": "run diagnostics",
            },
        )
        rollback = await client.post(
            "/tools/sys/governance/actions",
            json={
                "proposal_id": proposal_id,
                "action": "rollback",
                "acted_by": "human",
                "action_reason": "must be unavailable",
            },
        )

    assert applied.status_code == 200
    rollback_contract = applied.json()["item"]["transaction"]["rollback"]
    assert rollback_contract["supported"] is False
    assert "only tee overwrite" in rollback_contract["reason"]
    assert rollback.status_code == 409


@pytest.mark.asyncio
async def test_sys_governance_api_syncs_workspace_agent_handoff(monkeypatch, tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(tmp_path / "sys_governance_handoff.json"))
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(tmp_path / "sys_governance_handoff.jsonl"))
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    proposal = create_pending_sys_governance_proposal(
        command="tee",
        parameters={"command": "tee", "args": ["demo.txt"], "stdin_text": "ok\n"},
        capability="workspace_write",
        rationale="test automatic handoff",
        requested_by="workspace_agent",
        traceability={"request_id": "req-auto", "run_id": "run-auto", "source": "workspace_agent"},
        handoff={"state": "awaiting_decision", "step_id": "write"},
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        listed = await client.get("/tools/sys/governance/proposals", params={"decision": "pending"})
        assert listed.status_code == 200
        item = next(value for value in listed.json()["items"] if value["proposal_id"] == proposal["proposal_id"])
        assert item["handoff"]["state"] == "awaiting_decision"
        assert item["handoff"]["step_id"] == "write"

        decided = await client.post(
            "/tools/sys/governance/decisions",
            json={
                "proposal_id": proposal["proposal_id"],
                "decision": "approved",
                "decided_by": "test",
                "decision_reason": "approve exact staged action",
            },
        )
        assert decided.status_code == 200
        assert decided.json()["item"]["decision"] == "approved"


@pytest.mark.asyncio
async def test_workspace_handoff_approval_invokes_once_and_resumes(monkeypatch, tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    store_path = tmp_path / "sys_governance_auto_resume.json"
    events_path = tmp_path / "sys_governance_auto_resume.jsonl"
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(store_path))
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(events_path))
    executions = []

    async def _fake_execute_tool(self, request):
        executions.append(request)
        return ToolExecutionResult(
            tool_name="sys",
            status="success",
            output="created",
            execution_ms=1.0,
            metadata={"mutation_verified": True, "mutation_evidence": {"verified": True}},
        )

    monkeypatch.setattr("services.tools.coordinator.ToolCoordinator.execute_tool", _fake_execute_tool)

    class _ResumeResult:
        status = "completed"
        steps = [object()]
        validator = {"passed": True, "state": "completed"}

        def model_dump(self, **_kwargs):
            return {
                "status": self.status,
                "goal": "resume test",
                "steps": [{"step_id": "mkdir", "status": "success"}],
                "validator": self.validator,
            }

    class _WorkspaceAgent:
        def __init__(self):
            self.calls = []

        async def resume_from_governance_proposal(self, proposal, approved_execution):
            self.calls.append((proposal, approved_execution))
            return _ResumeResult()

        async def persist_run_artifact(self, result, *, session_id, run_id):
            return {"status": "success", "document_id": f"workspace_agent_run:{session_id}:{run_id}"}

    orchestrator = FakeOrchestrator()
    orchestrator.workspace_agent = _WorkspaceAgent()
    app = create_api_app(orchestrator=orchestrator, memory_adapter=adapter)
    proposal = create_pending_sys_governance_proposal(
        command="mkdir",
        parameters={
            "command": "mkdir",
            "args": ["-p", "/home/liara/workspace/resume-test"],
            "target_path": "/home/liara/workspace/resume-test",
            "request_id": "req-resume-api",
            "run_id": "run-resume-api",
            "session_id": "session-resume-api",
            "source": "workspace_agent",
            "context": "agent_workspace_mkdir",
        },
        capability="workspace_mkdir",
        rationale="resume exact approved workspace step",
        requested_by="workspace_agent",
        traceability={
            "request_id": "req-resume-api",
            "run_id": "run-resume-api",
            "session_id": "session-resume-api",
            "source": "workspace_agent",
        },
        handoff={
            "state": "awaiting_decision",
            "step_id": "mkdir",
            "checkpoint": {"schema_version": 1, "sentinel": "durable"},
        },
    )

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        decided = await client.post(
            "/tools/sys/governance/decisions",
            json={
                "proposal_id": proposal["proposal_id"],
                "decision": "approved",
                "decided_by": "human",
                "decision_reason": "resume the exact staged action",
            },
        )
        replay = await client.post(
            "/tools/sys/governance/decisions",
            json={
                "proposal_id": proposal["proposal_id"],
                "decision": "approved",
                "decided_by": "human",
                "decision_reason": "must not replay",
            },
        )
        rejected_proposal = create_pending_sys_governance_proposal(
            command="touch",
            parameters={"command": "touch", "args": ["/home/liara/workspace/rejected"]},
            capability="workspace_touch",
            rationale="verify rejected resume remains terminal",
            requested_by="workspace_agent",
            traceability={"request_id": "req-rejected", "run_id": "run-rejected"},
            handoff={
                "state": "awaiting_decision",
                "step_id": "touch",
                "checkpoint": {"schema_version": 1, "sentinel": "reject"},
            },
        )
        rejected = await client.post(
            "/tools/sys/governance/decisions",
            json={
                "proposal_id": rejected_proposal["proposal_id"],
                "decision": "rejected",
                "decided_by": "human",
                "decision_reason": "do not execute",
            },
        )

    assert decided.status_code == 200
    payload = decided.json()
    assert payload["workspace_resume"]["status"] == "completed"
    assert payload["item"]["handoff"]["state"] == "resume_completed"
    assert payload["item"]["invocation"]["attempt_count"] == 1
    assert len(executions) == 1
    assert len(orchestrator.workspace_agent.calls) == 1
    assert replay.status_code == 409
    assert rejected.status_code == 200
    assert rejected.json()["item"]["handoff"]["state"] == "rejected"
    assert len(executions) == 1
    assert len(orchestrator.workspace_agent.calls) == 1
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    approved_events = [
        event["event_type"]
        for event in events
        if event.get("proposal_id") == proposal["proposal_id"]
    ]
    assert approved_events[-3:] == [
        "invocation_attempted",
        "invocation_completed",
        "workspace_resume_completed",
    ]


@pytest.mark.asyncio
async def test_sys_governance_policy_block_cannot_be_approved(monkeypatch, tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(tmp_path / "sys_governance_blocked.json"))
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(tmp_path / "sys_governance_blocked.jsonl"))
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        proposal_resp = await client.post(
            "/tools/sys/governance/proposals",
            json={"command": "rm -rf /tmp/example", "requested_by": "test"},
        )
        proposal_id = proposal_resp.json()["item"]["proposal_id"]
        assert proposal_resp.json()["item"]["policy_check"]["allowed"] is False

        decision_resp = await client.post(
            "/tools/sys/governance/decisions",
            json={
                "proposal_id": proposal_id,
                "decision": "approved",
                "decided_by": "test",
                "decision_reason": "must remain blocked",
            },
        )
        assert decision_resp.status_code == 409
        assert "blocked by policy" in str(decision_resp.json().get("detail"))


@pytest.mark.asyncio
async def test_sys_governance_failed_attempt_consumes_default_approval(monkeypatch, tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )

    async def _failed_execute_tool(self, request):
        return ToolExecutionResult(
            tool_name=request.tool_name,
            status="failed",
            output=None,
            error="simulated tool failure",
            execution_ms=2.0,
        )

    monkeypatch.setattr("services.tools.coordinator.ToolCoordinator.execute_tool", _failed_execute_tool)
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(tmp_path / "sys_governance_failed.json"))
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(tmp_path / "sys_governance_failed.jsonl"))
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        proposal = await client.post("/tools/sys/governance/proposals", json={"command": "health"})
        proposal_id = proposal.json()["item"]["proposal_id"]
        await client.post(
            "/tools/sys/governance/decisions",
            json={
                "proposal_id": proposal_id,
                "decision": "approved",
                "decided_by": "test",
                "decision_reason": "exercise failed-attempt accounting",
            },
        )

        first = await client.post(
            "/tools/sys/invoke",
            json={"parameters": {"command": "health", "proposal_id": proposal_id}},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "failed"

        retry = await client.post(
            "/tools/sys/invoke",
            json={"parameters": {"command": "health", "proposal_id": proposal_id}},
        )
        assert retry.status_code == 409
        assert "limit reached" in str(retry.json().get("detail"))

        listed = await client.get("/tools/sys/governance/proposals")
        item = next(value for value in listed.json()["items"] if value["proposal_id"] == proposal_id)
        assert item["invocation"]["state"] == "failed"
        assert item["invocation"]["attempt_count"] == 1
        assert item["invocation"]["success_count"] == 0
        assert listed.json()["summary"]["consumed"] == 1


@pytest.mark.asyncio
async def test_sys_governance_enforcement_requires_proposal_id(monkeypatch, tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )

    monkeypatch.delenv("LIARA_SYS_GOVERNANCE_MODE", raising=False)
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_ENFORCE", "1")
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_STORE_PATH", str(tmp_path / "sys_governance_enforce.json"))
    monkeypatch.setenv("LIARA_SYS_GOVERNANCE_EVENTS_PATH", str(tmp_path / "sys_governance_enforce_events.jsonl"))
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        invoke_resp = await client.post(
            "/tools/sys/invoke",
            json={
                "parameters": {
                    "command": "health",
                },
                "simulation_mode": True,
            },
        )
        assert invoke_resp.status_code == 422
        assert "proposal_id" in str(invoke_resp.json().get("detail"))


def test_admin_sys_audit_summary_endpoint_supports_filters(tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    log_path = tmp_path / "sys_audit.jsonl"
    entries = [
        {
            "command": "curl",
            "args": ["https://example.com"],
            "policy_decision": "allowed",
            "exit_code": 0,
            "duration_ms": 10.0,
            "stdout_bytes": 100,
            "stderr_bytes": 0,
            "source": "orchestrator",
            "risk_level": "medium",
            "command_family": "network",
        },
        {
            "command": "ls",
            "args": ["-la"],
            "policy_decision": "allowed",
            "exit_code": 0,
            "duration_ms": 3.0,
            "stdout_bytes": 20,
            "stderr_bytes": 0,
            "source": "shell",
            "risk_level": "low",
            "command_family": "inspection",
        },
    ]
    with log_path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")

    with TestClient(app) as client:
        response = client.get(
            "/admin/sys-audit/summary",
            params={
                "log_path": str(log_path),
                "source": "orchestrator",
                "risk_level": "medium",
                "command_family": "network",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["summary"]["total"] == 1
    assert payload["summary"]["available_entries"] == 2
    assert payload["summary"]["filtered_entries"] == 1
    assert payload["summary"]["inspected_entries"] == 2


def test_admin_sys_audit_suspicious_endpoint_returns_blocked_entries(tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    log_path = tmp_path / "sys_audit.jsonl"
    entries = [
        {
            "command": "rm",
            "args": ["-rf", "/"],
            "policy_decision": "blocked",
            "policy_reason": "blocked by policy",
            "source": "api",
            "risk_level": "high",
            "command_family": "filesystem",
            "exit_code": None,
            "duration_ms": None,
            "stdout_bytes": None,
            "stderr_bytes": None,
        },
        {
            "command": "echo",
            "args": ["ok"],
            "policy_decision": "allowed",
            "exit_code": 0,
            "duration_ms": 1.0,
            "stdout_bytes": 2,
            "stderr_bytes": 0,
            "source": "shell",
            "risk_level": "low",
            "command_family": "other",
        },
    ]
    with log_path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")

    with TestClient(app) as client:
        response = client.get(
            "/admin/sys-audit/suspicious",
            params={
                "log_path": str(log_path),
                "source": "api",
                "max_items": 5,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["count"] == 1
    assert payload["items"][0]["command"] == "rm"


def test_admin_sys_audit_preset_top_risk_endpoint(tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    log_path = tmp_path / "sys_audit.jsonl"
    entries = [
        {
            "command": "curl",
            "args": ["https://example.com"],
            "policy_decision": "allowed",
            "exit_code": 0,
            "duration_ms": 9.0,
            "stdout_bytes": 42,
            "stderr_bytes": 0,
            "source": "orchestrator",
            "risk_level": "high",
            "command_family": "network",
        },
        {
            "command": "ls",
            "args": ["-la"],
            "policy_decision": "allowed",
            "exit_code": 0,
            "duration_ms": 1.0,
            "stdout_bytes": 10,
            "stderr_bytes": 0,
            "source": "shell",
            "risk_level": "low",
            "command_family": "inspection",
        },
    ]
    with log_path.open("w", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps(entry) + "\n")

    with TestClient(app) as client:
        response = client.get(
            "/admin/sys-audit/presets/top-risk",
            params={"log_path": str(log_path)},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["preset"] == "top-risk"
    assert payload["summary"]["total"] == 1
    assert payload["count"] == 1
    assert payload["items"][0]["command"] == "curl"


def test_admin_sys_audit_preset_unknown_returns_404(tmp_path):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    with TestClient(app) as client:
        response = client.get("/admin/sys-audit/presets/not-a-preset")

    assert response.status_code == 404
    payload = response.json()
    assert "available_presets" in payload["detail"]


def test_operations_workspace_returns_read_only_evidence(monkeypatch):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    monkeypatch.setattr(
        "services.api.routers.operations.get_workspace_status",
        lambda: {
            "workspace_root": "/home/liara/workspace",
            "artifacts_dir": "/home/liara/workspace/.liara_artifacts",
            "exists": True,
            "artifact_counts": {"validation": 2, "governance": 1, "memory": 0, "chat": 0},
        },
    )
    monkeypatch.setattr(
        "services.api.routers.operations.list_workspace_artifacts",
        lambda artifact_type=None, limit=10: [
            {
                "path": "/home/liara/workspace/.liara_artifacts/validation-reports/report.json",
                "type": "validation_report",
                "timestamp": "2026-07-15T10:00:00+00:00",
                "session_id": "session-a",
                "summary": {"job_id": "job-a", "findings_count": 0},
            }
        ][:limit],
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    with TestClient(app) as client:
        response = client.get(
            "/operations/workspace",
            params={"artifact_type": "validation", "limit": 5},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["workspace"]["artifact_counts"]["validation"] == 2
    assert payload["artifacts"][0]["summary"]["job_id"] == "job-a"
    assert payload["filters"] == {"artifact_type": "validation", "limit": 5}


def test_operations_workspace_rejects_unknown_artifact_type():
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    with TestClient(app) as client:
        response = client.get(
            "/operations/workspace",
            params={"artifact_type": "unknown"},
        )

    assert response.status_code == 422
    assert "allowed_types" in response.json()["detail"]


def test_operations_dreaming_returns_read_only_status_and_proposals(monkeypatch):
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )

    class FakeDreamingStore:
        async def dreaming_status(self):
            return MemoryDreamingStatusResponse(
                scheduler_enabled=False,
                mode="manual_only",
                last_run_id="dream-run-1",
                last_run_at="2026-08-07T20:00:00Z",
                last_run_state="completed",
                pending_staged_items=2,
                pending_proposals=1,
                status=MemoryServiceStatus(status="success", backend="postgres"),
            )

        async def dreaming_proposals(self, request):
            assert request.decision == "pending"
            assert request.limit == 20
            return MemoryDreamingProposalListResponse(
                items=[
                    MemoryDreamingProposalRecord(
                        proposal_id="dream-prop-1",
                        session_id="session-dream",
                        staging_id="stage-1",
                        target_namespace="dreaming",
                        target_key="candidate:stage-1",
                        proposed_value={"content": "candidate fact"},
                        proposed_status=MemoryLifecycleStatus.candidate,
                        promotion_reason="manual dreaming consolidation proposal",
                        evidence=[
                            MemoryEvidence(
                                source="session_summary",
                                confidence=0.8,
                                reference="stage-1",
                            ),
                            MemoryEvidence(
                                source="proposal_quality_signals",
                                reference="dream-prop-1",
                                metadata={
                                    "schema_version": 1,
                                    "interpretation": "validator_evidence_only",
                                    "complexity": {
                                        "score": 0.42,
                                        "level": "moderate",
                                        "character_count": 240,
                                        "line_count": 4,
                                        "declared_source_count": 2,
                                        "evidence_count": 3,
                                        "accepted_relation_count": 1,
                                    },
                                    "coverage": {
                                        "status": "measured",
                                        "source_coverage_ratio": 1.0,
                                        "relation_coverage_ratio": 0.5,
                                        "uncovered_source_ids": [],
                                        "relation_uncovered_source_ids": ["message:2"],
                                    },
                                },
                            ),
                            MemoryEvidence(
                                source="validator_report",
                                confidence=1.0,
                                reference="validator-job-1",
                                metadata={
                                    "verdict": "passed",
                                    "findings_count": 0,
                                    "highest_severity": "none",
                                    "artifacts": ["artifacts/validator_jobs/validator-job-1/report.json"],
                                },
                            )
                        ],
                        decision="pending",
                        created_at="2026-08-07T20:01:00Z",
                        metadata={
                            "assurance_required": True,
                            "assurance_verdict": "passed",
                            "assurance_job_id": "validator-job-1",
                        },
                    )
                ],
                status=MemoryServiceStatus(status="success", backend="postgres"),
            )

        async def close(self):
            return None

    monkeypatch.setattr("services.api.routers.operations.BackedMemoryServiceStore", FakeDreamingStore)
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    with TestClient(app) as client:
        response = client.get(
            "/operations/dreaming",
            params={"decision": "pending", "limit": 20},
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["mode"] == "manual_only"
    assert payload["scheduler_enabled"] is False
    assert payload["pending_staged_items"] == 2
    assert payload["pending_proposals"] == 1
    assert payload["proposal_count"] == 1
    assert payload["proposals"][0]["proposal_id"] == "dream-prop-1"
    assert payload["proposals"][0]["assurance"]["verdict"] == "passed"
    assert payload["proposals"][0]["assurance"]["validator_job_id"] == "validator-job-1"
    assert payload["proposals"][0]["assurance"]["artifacts"][0]["path"].endswith("report.json")
    quality = payload["proposals"][0]["quality_signals"]
    assert quality["available"] is True
    assert quality["schema_version"] == 1
    assert quality["complexity"]["level"] == "moderate"
    assert quality["complexity"]["score"] == 0.42
    assert quality["coverage"]["source_coverage_ratio"] == 1.0
    assert quality["coverage"]["relation_coverage_ratio"] == 0.5
    assert quality["coverage"]["relation_uncovered_source_ids"] == ["message:2"]
    assert payload["assurance"] == {
        "required": 1,
        "blocked": 0,
        "verdicts": {"pending": 0, "passed": 1, "attention": 0, "failed": 0},
    }
    assert payload["quality_signals"] == {
        "available": 1,
        "complexity_levels": {"low": 0, "moderate": 1, "high": 0},
    }
    assert payload["filters"] == {"decision": "pending", "limit": 20}


def test_admin_llama_backends_lists_all_three_builds():
    """GET /admin/llama-backends returns all three build variants with present=True."""
    from unittest.mock import patch, MagicMock
    import pathlib

    def _fake_find(preferred_variant="auto"):
        return ("sycl-fp16-intel-arc", pathlib.Path("C:/ai/LIARA/llama-builds-final/sycl-fp16-intel-arc/llama-server.exe"))

    def _fake_get(variant):
        return pathlib.Path(f"C:/ai/LIARA/llama-builds-final/{variant}/llama-server.exe")

    from services.inference.llama_cpp_server import LlamaCppServerManager
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    with patch.object(LlamaCppServerManager, "get_build_path", side_effect=_fake_get), \
         patch.object(LlamaCppServerManager, "find_available_build", side_effect=_fake_find):
        with TestClient(app) as client:
            response = client.get("/admin/llama-backends")

    assert response.status_code == 200
    payload = response.json()
    assert "available_builds" in payload
    variants = [b["variant"] for b in payload["available_builds"]]
    assert "sycl-fp16-intel-arc" in variants
    assert "vulkan-cross-gpu" in variants
    assert "cpu-avx2-f16c" in variants
    assert payload["active_variant"] == "sycl-fp16-intel-arc"
    assert all(b["present"] for b in payload["available_builds"])


def test_admin_llama_backends_missing_build_shows_present_false():
    """When a build binary is absent, present=False for that variant."""
    import pathlib
    from services.inference.llama_cpp_server import LlamaCppServerManager

    def _selective_get(variant):
        if variant == "sycl-fp16-intel-arc":
            raise FileNotFoundError("not found")
        return pathlib.Path(f"C:/ai/LIARA/llama-builds-final/{variant}/llama-server.exe")

    def _fake_find(preferred_variant="auto"):
        return ("vulkan-cross-gpu", pathlib.Path("C:/ai/LIARA/llama-builds-final/vulkan-cross-gpu/llama-server.exe"))

    from unittest.mock import patch
    adapter = InProcessMemoryAdapter(
        MemoryLayer(
            session_store=EphemeralMemoryStore(),
            fact_store=EphemeralMemoryStore(),
            retrieval_index=EphemeralMemoryStore(),
            graph_store=NullMemoryStore(),
        )
    )
    app = create_api_app(orchestrator=FakeOrchestrator(), memory_adapter=adapter)

    with patch.object(LlamaCppServerManager, "get_build_path", side_effect=_selective_get), \
         patch.object(LlamaCppServerManager, "find_available_build", side_effect=_fake_find):
        with TestClient(app) as client:
            response = client.get("/admin/llama-backends")

    assert response.status_code == 200
    payload = response.json()
    sycl = next(b for b in payload["available_builds"] if b["variant"] == "sycl-fp16-intel-arc")
    assert sycl["present"] is False
    assert payload["active_variant"] == "vulkan-cross-gpu"
