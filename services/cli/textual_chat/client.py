from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx

from .cache import ClientCache
from .models import ChatReply, ChatSettings


def _chat_payload(settings: ChatSettings, message: str) -> dict[str, Any]:
    return {
        "session_id": settings.session_id,
        "user_id": settings.user_id,
        "message": message,
        "max_tokens": settings.max_tokens,
    }


class LiaraApiClient:
    """Async API wrapper used by the Textual chat app."""

    def __init__(self, settings: ChatSettings):
        self.settings = settings
        self.cache = ClientCache(settings.cache_dir)
        self._client = httpx.AsyncClient(
            base_url=self.settings.base_url.rstrip("/"), timeout=self.settings.timeout
        )
        self._save_settings()

    def _save_settings(self) -> None:
        self.cache.save_settings(
            {
                "base_url": self.settings.base_url,
                "session_id": self.settings.session_id,
                "user_id": self.settings.user_id,
                "max_tokens": self.settings.max_tokens,
                "mode": self.settings.mode,
            }
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _cache_key(self, key: str, payload: dict[str, Any]) -> str:
        return f"{key}:{json.dumps(payload, ensure_ascii=True, sort_keys=True)}"

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def send_chat(self, message: str) -> ChatReply:
        payload = _chat_payload(self.settings, message)
        response = await self._client.post("/chat", json=payload)
        response.raise_for_status()
        data = response.json()
        self._save_settings()
        self.cache.invalidate_prefix("history", f"{self.settings.session_id}:")
        return ChatReply(text=str(data.get("response", "")).strip(), payload=data)

    async def send_stream(
        self,
        message: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> ChatReply:
        """Send message via streaming endpoint with optional progress callback.
        
        Args:
            message: The message to send.
            progress_callback: Optional callback for progress events (orchestration_complete, memory_effect_detected, etc).
        """
        payload = _chat_payload(self.settings, message)
        chunks: list[str] = []
        final_payload: dict[str, Any] | None = None

        async with self._client.stream("POST", "/chat/stream", json=payload) as response:
            response.raise_for_status()
            current_event: str | None = None
            async for raw_line in response.aiter_lines():
                line = (raw_line or "").strip()
                if not line:
                    continue
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                data_raw = line.split(":", 1)[1].strip()
                if current_event == "progress":
                    try:
                        progress_obj = json.loads(data_raw)
                        if progress_callback:
                            progress_callback(progress_obj)
                    except json.JSONDecodeError:
                        continue
                elif current_event == "chunk":
                    try:
                        payload_obj = json.loads(data_raw)
                        chunks.append(str(payload_obj.get("text", "")))
                    except json.JSONDecodeError:
                        continue
                elif current_event == "final":
                    try:
                        final_payload = json.loads(data_raw)
                    except json.JSONDecodeError:
                        final_payload = None
                elif current_event == "done":
                    break

        text = "".join(chunks).strip()
        if not text and final_payload:
            text = str(final_payload.get("response", "")).strip()
        self._save_settings()
        self.cache.invalidate_prefix("history", f"{self.settings.session_id}:")
        return ChatReply(text=text, payload=final_payload or {})

    async def get_history(self, session_id: str, limit: int) -> dict[str, Any]:
        params = {
            "session_id": session_id,
            "limit": int(limit),
            "include_tool_messages": "false",
        }
        cache_key = self._cache_key(f"{session_id}:{int(limit)}", params)
        cached = self.cache.get_cached("history", cache_key)
        if cached is not None:
            return cached
        data = await self._get_json("/history", params=params)
        self.cache.set_cached("history", cache_key, data, ttl_seconds=4)
        return data

    async def get_health(self) -> dict[str, Any]:
        cache_key = self._cache_key("health", {})
        cached = self.cache.get_cached("health", cache_key)
        if cached is not None:
            return cached
        data = await self._get_json("/health")
        self.cache.set_cached("health", cache_key, data, ttl_seconds=5)
        return data

    async def get_tools(self) -> dict[str, Any]:
        cache_key = self._cache_key("tools", {})
        cached = self.cache.get_cached("tools", cache_key)
        if cached is not None:
            return cached
        data = await self._get_json("/tools")
        self.cache.set_cached("tools", cache_key, data, ttl_seconds=45)
        return data

    def append_transcript(self, role: str, text: str, kind: str = "chat") -> None:
        self.cache.append_transcript(self.settings.session_id, role, text, kind=kind)

    def get_cached_transcript(self) -> list[dict[str, str]]:
        return self.cache.get_transcript(self.settings.session_id)

    def clear_transcript(self) -> None:
        self.cache.clear_transcript(self.settings.session_id)

    def clear_api_cache(self) -> None:
        self.cache.clear_api()

    def cache_summary(self) -> dict[str, Any]:
        return self.cache.summary()
