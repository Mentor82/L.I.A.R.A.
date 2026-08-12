"""Archived web search tool powered by DuckDuckGo.

Primary path: ddgs Python library.
Fallback path: WSL curl (bypasses Windows-side bot-detection on DuckDuckGo).
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import time
import urllib.parse
from typing import Any

from ddgs import DDGS

from ..base import Tool

_WSL_DISTRO = os.getenv("WSL_DISTRO", "Debian")
_DDG_HTML_URL = "https://html.duckduckgo.com/html/?q={query}"
_RESULT_RE = re.compile(
    r'class="result__title"[^>]*>.*?<a[^>]+href="[^"]*uddg=([^"&]+)[^"]*"[^>]*>(.*?)</a>.*?'
    r'class="result__snippet"[^>]*>(.*?)</span>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _wsl_available() -> bool:
    return shutil.which("wsl") is not None


def _parse_ddg_html(html: str, max_results: int) -> list[dict[str, str]]:
    """Extract title/url/snippet from DuckDuckGo HTML response."""
    results: list[dict[str, str]] = []
    # Extract URLs and snippets via simple pattern matching (no lxml dependency).
    urls = re.findall(r'uddg=([^"&\s]+)', html)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</span>', html, re.DOTALL)
    titles_raw = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.DOTALL)
    for url_enc, title_raw, snip_raw in zip(urls, titles_raw, snippets):
        title = _TAG_RE.sub("", title_raw).strip()
        snippet = _TAG_RE.sub("", snip_raw).strip()
        try:
            url = urllib.parse.unquote(url_enc)
        except Exception:
            url = url_enc
        if title or snippet:
            results.append({"title": title, "url": url, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def _wsl_search(query: str, max_results: int) -> list[dict[str, str]]:
    """Run DuckDuckGo HTML search via WSL curl as bot-bypass fallback."""
    encoded = urllib.parse.quote_plus(query)
    url = _DDG_HTML_URL.format(query=encoded)
    cmd = [
        "wsl", "-d", _WSL_DISTRO, "--",
        "curl", "-s", "-L",
        "-A", "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
        "--max-time", "15",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    return _parse_ddg_html(proc.stdout, max_results)


class WebSearchTool(Tool):
    """Search the web for current information."""

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return "Search the web for current information using DuckDuckGo"

    @property
    def required_parameters(self) -> list[str]:
        return ["query"]

    @property
    def optional_parameters(self) -> list[str]:
        return ["max_results", "region"]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Execute web search. Falls back to WSL curl if ddgs is bot-blocked."""
        self._validate_parameters(**kwargs)

        query = kwargs["query"]
        max_results: int = int(kwargs.get("max_results", 5))
        region: str = kwargs.get("region", "wt-wt")

        t0 = time.monotonic()
        results: list[dict[str, str]] = []
        backend = "ddgs"

        # Primary: ddgs library
        try:
            raw = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: list(DDGS().text(query, region=region, max_results=max_results)),
            )
            results = [
                {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
                for r in raw
            ]
        except Exception:
            raw = []

        # Fallback: WSL curl when ddgs returns empty (bot-blocked on Windows)
        if not results and _wsl_available():
            backend = f"wsl_{_WSL_DISTRO.lower()}_curl"
            try:
                results = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: _wsl_search(query, max_results),
                )
            except Exception:
                pass

        latency_ms = int((time.monotonic() - t0) * 1000)

        if not results:
            return self.failure(f"No results found via {backend} (query={query!r})")

        return self.success(
            {"query": query, "results": results, "count": len(results)},
            {"latency_ms": latency_ms, "backend": backend},
        )
