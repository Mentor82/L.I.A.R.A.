"""LIARA tool for native-WSL session lifecycle management."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from ..base import Tool
from services.simulation.wsl_session_runtime import WslSessionError, WslSessionManager


class WslSessionTool(Tool):
    """Create snapshots, collect candidates, validate them, and clean up."""

    @property
    def name(self) -> str:
        return "wsl_session"

    @property
    def description(self) -> str:
        return (
            "Manage isolated native-WSL test/simulation sessions. "
            "Execution itself remains on the existing sys tool."
        )

    @property
    def required_parameters(self) -> list[str]:
        return ["action"]

    @property
    def optional_parameters(self) -> list[str]:
        return [
            "session_id",
            "label",
            "scope",
            "strict_mode",
            "request_id",
            "run_id",
            "trace_session_id",
            "source",
            "context",
        ]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self._validate_parameters(**kwargs)
        action = str(kwargs["action"] or "").strip().lower()
        manager = WslSessionManager()
        try:
            if action == "create":
                result = await asyncio.to_thread(
                    manager.create,
                    label=str(kwargs.get("label") or "simulation"),
                    request_id=kwargs.get("request_id"),
                    run_id=kwargs.get("run_id"),
                    trace_session_id=kwargs.get("trace_session_id"),
                    source=kwargs.get("source"),
                )
                return self.success(result)
            if action == "plan":
                return self.success(await asyncio.to_thread(manager.plan))

            session_id = str(kwargs.get("session_id") or "")
            if not session_id:
                return self.failure("session_id is required for this action")

            if action == "status":
                return self.success(await asyncio.to_thread(manager.status, session_id))
            if action == "collect":
                return self.success(await asyncio.to_thread(manager.collect, session_id))
            if action == "destroy":
                return self.success(await asyncio.to_thread(manager.destroy, session_id))
            if action == "validate":
                status = await asyncio.to_thread(manager.status, session_id)
                collection = status.get("latest_collection")
                if not collection:
                    collection = await asyncio.to_thread(manager.collect, session_id)
                request_payload = dict(collection["validator_request"])
                request_payload["scope"] = str(kwargs.get("scope") or "quick")
                request_payload["strict_mode"] = bool(kwargs.get("strict_mode", False))
                base_url = os.getenv("LIARA_MEMORY_SERVICE_URL", "http://127.0.0.1:8020").rstrip("/")
                async with httpx.AsyncClient(timeout=30) as client:
                    response = await client.post(f"{base_url}/validator/submit", json=request_payload)
                    response.raise_for_status()
                return self.success(
                    {
                        "session_id": session_id,
                        "collection": collection,
                        "validator_job": response.json(),
                    }
                )
            return self.failure("unsupported action; use plan, create, status, collect, validate, or destroy")
        except (WslSessionError, OSError, httpx.HTTPError) as exc:
            return self.failure(str(exc))
