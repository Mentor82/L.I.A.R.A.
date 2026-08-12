"""Archived session context lookup via generic history endpoint."""

from __future__ import annotations

import os
from typing import Any

import httpx

from ..base import Tool


_DEFAULT_API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8010")


class SessionContextTool(Tool):
    """Fetch recent session history for recall and continuity questions."""

    @property
    def name(self) -> str:
        return "session_context"

    @property
    def description(self) -> str:
        return "Fetch recent conversation history for the current session"

    @property
    def required_parameters(self) -> list[str]:
        return ["session_id"]

    @property
    def optional_parameters(self) -> list[str]:
        return ["limit", "include_tool_messages", "base_url"]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self._validate_parameters(**kwargs)

        session_id = str(kwargs["session_id"])
        limit = max(1, min(int(kwargs.get("limit", 8)), 50))
        include_tool_messages = bool(kwargs.get("include_tool_messages", False))
        base_url = str(kwargs.get("base_url", _DEFAULT_API_BASE_URL)).rstrip("/")

        try:
            async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as client:
                response = await client.get(
                    "/history",
                    params={
                        "session_id": session_id,
                        "limit": limit,
                        "include_tool_messages": str(include_tool_messages).lower(),
                    },
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            return self.failure(str(exc))

        items = payload.get("items", [])
        compact_items = [
            {
                "role": item.get("role", "unknown"),
                "content": item.get("content", ""),
                "created_at": item.get("created_at"),
            }
            for item in items
        ]
        summary = "\n".join(f"{item['role']}: {item['content'][:240]}" for item in compact_items)

        return self.success(
            {
                "session_id": session_id,
                "count": len(compact_items),
                "items": compact_items,
                "summary": summary,
            }
        )