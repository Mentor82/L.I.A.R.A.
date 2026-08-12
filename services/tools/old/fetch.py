"""Archived HTTP fetch tool — retrieves a URL and returns the text body."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx

from ..base import Tool


_ALLOWED_SCHEMES = {"http", "https"}
_MAX_BYTES = 512_000  # 512 KB safety cap


class FetchTool(Tool):
    """Fetch a public URL and return its text content."""

    @property
    def name(self) -> str:
        return "fetch"

    @property
    def description(self) -> str:
        return "Fetch a public HTTP/HTTPS URL and return its text content"

    @property
    def required_parameters(self) -> list[str]:
        return ["url"]

    @property
    def optional_parameters(self) -> list[str]:
        return ["timeout"]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self._validate_parameters(**kwargs)

        url: str = kwargs["url"]
        timeout: float = float(kwargs.get("timeout", 15))

        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            return self.failure(f"Unsupported scheme '{parsed.scheme}'. Only http/https allowed.")
        if not parsed.netloc:
            return self.failure("Invalid URL: missing host.")

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                response = await client.get(url, headers={"User-Agent": "generic-fetch-tool/1.0"})
                response.raise_for_status()
                text = response.text[:_MAX_BYTES]
        except httpx.HTTPStatusError as e:
            return self.failure(f"HTTP {e.response.status_code}: {url}")
        except Exception as e:
            return self.failure(str(e))

        latency_ms = int((time.monotonic() - t0) * 1000)
        return self.success(
            {"url": url, "content": text, "length": len(text)},
            {"latency_ms": latency_ms, "status_code": response.status_code},
        )
