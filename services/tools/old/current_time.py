"""Built-in: Current time tool."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..base import Tool


class CurrentTimeTool(Tool):
    """Get current date/time in specified timezone."""

    @property
    def name(self) -> str:
        return "current_time"

    @property
    def description(self) -> str:
        return "Get current UTC date and time"

    @property
    def required_parameters(self) -> list[str]:
        return []

    @property
    def optional_parameters(self) -> list[str]:
        return ["timezone"]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Get current time."""
        try:
            now = datetime.now(tz=timezone.utc)
            return self.success(
                {
                    "iso": now.isoformat(),
                    "timestamp": now.timestamp(),
                    "human_readable": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
                }
            )
        except Exception as e:
            return self.failure(str(e))
