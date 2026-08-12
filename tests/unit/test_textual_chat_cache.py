from __future__ import annotations

import httpx
import pytest

from services.cli.textual_chat.cache import ClientCache
from services.cli.textual_chat.client import LiaraApiClient
from services.cli.textual_chat.models import ChatSettings


def test_client_cache_persists_transcript_and_limits_entries(tmp_path):
    cache = ClientCache(tmp_path, transcript_limit=3)

    cache.append_transcript("session-a", "user", "one")
    cache.append_transcript("session-a", "assistant", "two")
    cache.append_transcript("session-a", "user", "three")
    cache.append_transcript("session-a", "assistant", "four")

    transcript = cache.get_transcript("session-a")

    assert [entry["text"] for entry in transcript] == ["two", "three", "four"]
    assert cache.summary()["transcript_sessions"] == 1


def test_client_cache_expires_api_entries(tmp_path):
    cache = ClientCache(tmp_path)
    cache.set_cached("health", "health:{}", {"status": "ok"}, ttl_seconds=1)

    assert cache.get_cached("health", "health:{}") == {"status": "ok"}

    stale_state = cache.state_path.read_text(encoding="utf-8").replace('"expires_at": ', '"expires_at": -1')
    cache.state_path.write_text(stale_state, encoding="utf-8")
    expired = ClientCache(tmp_path)

    assert expired.get_cached("health", "health:{}") is None
    assert expired.summary()["misses"] >= 1


@pytest.mark.asyncio
async def test_liara_api_client_caches_health_requests(tmp_path):
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(200, json={"status": "ok", "service": "liara-api"})

    settings = ChatSettings(
        base_url="http://127.0.0.1:8010",
        timeout=10,
        session_id="session-a",
        user_id="user-a",
        max_tokens=512,
        cache_dir=str(tmp_path),
    )
    client = LiaraApiClient(settings)
    await client.aclose()
    client._client = httpx.AsyncClient(
        base_url=settings.base_url.rstrip("/"),
        timeout=settings.timeout,
        transport=httpx.MockTransport(handler),
    )

    first = await client.get_health()
    second = await client.get_health()
    summary = client.cache_summary()

    await client.aclose()

    assert first == second == {"status": "ok", "service": "liara-api"}
    assert request_count == 1
    assert summary["hits"] >= 1