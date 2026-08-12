"""Built-in: Orientation tool for LIARA self-description."""

from __future__ import annotations

from typing import Any

from services.config import Settings

from ..base import Tool
from .sys_command_policy import list_high_risk_blocked_commands, list_profiled_command_names


def _configured_backends() -> dict[str, bool]:
    return {
        "postgres": bool(Settings.POSTGRES_URL),
        "redis": bool(Settings.REDIS_URL),
        "qdrant": bool(Settings.QDRANT_URL),
        "chroma": bool(Settings.CHROMA_HOST),
        "neo4j": bool(Settings.NEO4J_URL),
        "embedding": bool(Settings.EMBEDDING_SERVICE_BASE_URL),
    }


async def _collect_backend_health_snapshot() -> dict[str, Any]:
    from services.memory.store import BackedMemoryServiceStore

    configured = _configured_backends()
    if not any(configured.values()):
        return {
            "probe": "skipped",
            "reason": "no_backends_configured",
            "configured": configured,
            "backend_health": {},
        }

    store = None
    try:
        store = BackedMemoryServiceStore()
        response = await store.health_backends()
        return {
            "probe": "success",
            "configured": configured,
            "backend_health": dict(response.backend_health),
            "status": response.status.status,
            "degraded": bool(response.status.degraded),
            "error": response.status.error,
        }
    except Exception as exc:
        return {
            "probe": "failed",
            "configured": configured,
            "backend_health": {},
            "error": str(exc),
        }
    finally:
        if store is not None:
            await store.close()


class OrientationTool(Tool):
    """Explain what LIARA is, what it can do, and its current operating model."""

    @property
    def name(self) -> str:
        return "orientation"

    @property
    def description(self) -> str:
        return "Describe LIARA's role, capabilities, and tool-based working model"

    @property
    def required_parameters(self) -> list[str]:
        return []

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self._validate_parameters(**kwargs)

        try:
            backend_awareness = await _collect_backend_health_snapshot()
        except Exception as exc:
            backend_awareness = {
                "probe": "failed",
                "configured": _configured_backends(),
                "backend_health": {},
                "error": str(exc),
            }

        from ..registry import get_tool_registry

        registry = get_tool_registry()
        tool_names = sorted(registry.list_tools())

        return self.success(
            {
                "name": "LIARA",
                "role": "Local AI assistant with tool use, session memory, and workspace awareness",
                "core_capabilities": [
                    "Answer questions with conversation context",
                    "Search the web for fresh information",
                    "Fetch remote pages",
                    "Read local workspace files",
                    "List files in the workspace",
                    "Use persisted session history when configured",
                ],
                "working_style": [
                    "Prefer conversation history first for recall questions",
                    "Use tools when external evidence or workspace grounding is needed",
                    "Keep track of session context across turns",
                ],
                "limits": [
                    "Cannot guarantee factual correctness without strong grounding",
                    "Tool access is limited to registered tools and allowed workspace boundaries",
                ],
                "system_awareness": {
                    "runtime": {
                        "memory_mode": Settings.MEMORY_MODE,
                        "memory_adapter_only": bool(Settings.MEMORY_ADAPTER_ONLY),
                        "memory_service_base_url": Settings.MEMORY_SERVICE_BASE_URL,
                        "embedding_service_base_url": Settings.EMBEDDING_SERVICE_BASE_URL,
                    },
                    "registered_tools": {
                        "count": len(tool_names),
                        "names": tool_names,
                    },
                    "memory_backends": backend_awareness,
                    "safety_controls": {
                        "profiled_sys_commands": sorted(list_profiled_command_names()),
                        "blocked_high_risk_commands": sorted(list_high_risk_blocked_commands()),
                    },
                },
            }
        )