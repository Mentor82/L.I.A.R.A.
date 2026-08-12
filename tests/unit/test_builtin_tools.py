"""Unit tests for FetchTool, ReadFileTool, and real WebSearchTool wiring."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.tools.old.fetch import FetchTool
from services.tools.old.list_files import ListFilesTool
from services.tools.builtin.orientation import OrientationTool
from services.tools.old.read_file import ReadFileTool
from services.tools.old.session_context import SessionContextTool
from services.tools.old.web_search import WebSearchTool
from services.tools.registry import get_tool_registry


@pytest.fixture(autouse=True)
def _default_local_sandbox_mode(monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")


# ---------------------------------------------------------------------------
# WebSearchTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_search_returns_results():
    fake_results = [
        {"title": "LIARA project", "href": "https://example.com/liara", "body": "A local AI assistant."},
        {"title": "Another page", "href": "https://example.com/other", "body": "More info."},
    ]
    with patch("services.tools.old.web_search.DDGS") as MockDDGS:
        MockDDGS.return_value.text.return_value = fake_results
        tool = WebSearchTool()
        result = await tool.execute(query="LIARA", max_results=2)

    assert result["status"] == "success"
    assert result["output"]["count"] == 2
    assert result["output"]["results"][0]["url"] == "https://example.com/liara"
    assert result["output"]["results"][0]["title"] == "LIARA project"


@pytest.mark.asyncio
async def test_web_search_ddgs_error_returns_failure():
    with patch("services.tools.old.web_search.DDGS") as MockDDGS, \
         patch("services.tools.old.web_search._wsl_available", return_value=False):
        MockDDGS.return_value.text.side_effect = RuntimeError("network error")
        tool = WebSearchTool()
        result = await tool.execute(query="fail")

    assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# FetchTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_returns_text_content():
    mock_response = MagicMock()
    mock_response.text = "<html>Hello LIARA</html>"
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("services.tools.old.fetch.httpx.AsyncClient", return_value=mock_client):
        tool = FetchTool()
        result = await tool.execute(url="https://example.com")

    assert result["status"] == "success"
    assert "Hello LIARA" in result["output"]["content"]
    assert result["output"]["url"] == "https://example.com"


@pytest.mark.asyncio
async def test_fetch_rejects_non_http_scheme():
    tool = FetchTool()
    result = await tool.execute(url="ftp://example.com/file")
    assert result["status"] == "failed"
    assert "Unsupported scheme" in result["error"]


@pytest.mark.asyncio
async def test_fetch_rejects_missing_host():
    tool = FetchTool()
    result = await tool.execute(url="https:///no-host")
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_fetch_handles_http_error():
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Not Found", request=MagicMock(), response=mock_response
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("services.tools.old.fetch.httpx.AsyncClient", return_value=mock_client):
        tool = FetchTool()
        result = await tool.execute(url="https://example.com/missing")

    assert result["status"] == "failed"
    assert "404" in result["error"]


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_file_returns_content():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        test_file = root / "notes.txt"
        test_file.write_text("Hello from LIARA", encoding="utf-8")

        tool = ReadFileTool(allowed_root=root)
        result = await tool.execute(path="notes.txt")

    assert result["status"] == "success"
    assert result["output"]["content"] == "Hello from LIARA"
    assert result["output"]["path"] == "notes.txt"


@pytest.mark.asyncio
async def test_read_file_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        tool = ReadFileTool(allowed_root=root)
        result = await tool.execute(path="../../etc/passwd")

    assert result["status"] == "failed"
    assert "Access denied" in result["error"]


@pytest.mark.asyncio
async def test_read_file_not_found():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        tool = ReadFileTool(allowed_root=root)
        result = await tool.execute(path="nonexistent.txt")

    assert result["status"] == "failed"
    assert "not found" in result["error"].lower()


@pytest.mark.asyncio
async def test_read_file_uses_session_sandbox_root():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "workspace").mkdir()
        (root / "workspace" / "notes.txt").write_text("scoped", encoding="utf-8")

        tool = ReadFileTool(allowed_root=root)
        result = await tool.execute(path="notes.txt", sandbox_root="workspace")

    assert result["status"] == "success"
    assert result["output"]["content"] == "scoped"
    assert result["metadata"]["sandbox_root"].endswith("workspace")


# ---------------------------------------------------------------------------
# OrientationTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orientation_returns_capabilities():
    tool = OrientationTool()
    result = await tool.execute()

    assert result["status"] == "success"
    assert result["output"]["name"] == "LIARA"
    assert "core_capabilities" in result["output"]
    assert "tool use" in result["output"]["role"].lower()
    assert "system_awareness" in result["output"]
    assert "registered_tools" in result["output"]["system_awareness"]


@pytest.mark.asyncio
async def test_orientation_reports_dynamic_awareness_payload():
    tool = OrientationTool()

    with patch(
        "services.tools.builtin.orientation._collect_backend_health_snapshot",
        new=AsyncMock(
            return_value={
                "probe": "success",
                "configured": {"postgres": True},
                "backend_health": {"postgres": "healthy"},
                "status": "success",
                "degraded": False,
                "error": None,
            }
        ),
    ):
        result = await tool.execute()

    assert result["status"] == "success"
    awareness = result["output"]["system_awareness"]
    assert awareness["memory_backends"]["probe"] == "success"
    assert awareness["memory_backends"]["backend_health"]["postgres"] == "healthy"
    assert awareness["registered_tools"]["count"] >= 1
    assert "orientation" in awareness["registered_tools"]["names"]
    assert "profiled_sys_commands" in awareness["safety_controls"]


@pytest.mark.asyncio
async def test_orientation_gracefully_handles_probe_exception():
    tool = OrientationTool()

    with patch(
        "services.tools.builtin.orientation._collect_backend_health_snapshot",
        new=AsyncMock(side_effect=RuntimeError("probe exploded")),
    ):
        result = await tool.execute()

    assert result["status"] == "success"
    backends = result["output"]["system_awareness"]["memory_backends"]
    assert backends["probe"] == "failed"
    assert backends["backend_health"] == {}
    assert "probe exploded" in backends["error"]


@pytest.mark.asyncio
async def test_session_context_returns_recent_history():
    payload = {
        "items": [
            {"role": "user", "content": "Hallo", "created_at": "2026-04-15T00:00:00Z"},
            {"role": "assistant", "content": "Hi", "created_at": "2026-04-15T00:00:01Z"},
        ]
    }

    mock_response = MagicMock()
    mock_response.json.return_value = payload
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("services.tools.old.session_context.httpx.AsyncClient", return_value=mock_client):
        tool = SessionContextTool()
        result = await tool.execute(session_id="session-1", limit=2)

    assert result["status"] == "success"
    assert result["output"]["count"] == 2
    assert "user: Hallo" in result["output"]["summary"]


# ---------------------------------------------------------------------------
# ListFilesTool
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_files_returns_directory_entries():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "docs").mkdir()
        (root / "notes.txt").write_text("hello", encoding="utf-8")

        tool = ListFilesTool(allowed_root=root)
        result = await tool.execute()

    assert result["status"] == "success"
    assert result["output"]["count"] == 2
    assert {entry["path"] for entry in result["output"]["entries"]} == {"docs", "notes.txt"}


@pytest.mark.asyncio
async def test_list_files_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        tool = ListFilesTool(allowed_root=root)
        result = await tool.execute(path="../../")

    assert result["status"] == "failed"
    assert "Access denied" in result["error"]


@pytest.mark.asyncio
async def test_list_files_recursive_respects_max_entries():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "a").mkdir()
        (root / "a" / "one.txt").write_text("1", encoding="utf-8")
        (root / "a" / "two.txt").write_text("2", encoding="utf-8")

        tool = ListFilesTool(allowed_root=root)
        result = await tool.execute(path="a", recursive=True, max_entries=1)

    assert result["status"] == "success"
    assert result["output"]["count"] == 1


@pytest.mark.asyncio
async def test_list_files_filters_by_pattern_and_type():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "docs").mkdir()
        (root / "docs" / "guide.md").write_text("guide", encoding="utf-8")
        (root / "docs" / "draft.txt").write_text("draft", encoding="utf-8")

        tool = ListFilesTool(allowed_root=root)
        result = await tool.execute(path="docs", recursive=True, pattern="*.md", entry_type="file")

    assert result["status"] == "success"
    assert result["output"]["count"] == 1
    assert result["output"]["entries"][0]["path"] == "docs/guide.md"


@pytest.mark.asyncio
async def test_list_files_uses_session_sandbox_root():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "frontend").mkdir()
        (root / "frontend" / "index.html").write_text("<html></html>", encoding="utf-8")

        tool = ListFilesTool(allowed_root=root)
        result = await tool.execute(sandbox_root="frontend")

    assert result["status"] == "success"
    assert result["output"]["root"].endswith("frontend")
    assert result["output"]["entries"][0]["path"] == "index.html"


@pytest.mark.asyncio
async def test_read_file_accepts_canonical_wsl_sandbox_root(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "wsl")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_ROOT", "/home/liara/workspace")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_WINDOWS_ROOT", str(tmp_path))

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "notes.txt").write_text("from wsl canonical root", encoding="utf-8")

    tool = ReadFileTool()
    result = await tool.execute(path="notes.txt", sandbox_root="/home/liara/workspace/docs")

    assert result["status"] == "success"
    assert result["output"]["content"] == "from wsl canonical root"


@pytest.mark.asyncio
async def test_list_files_accepts_canonical_wsl_sandbox_root(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "wsl")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_ROOT", "/home/liara/workspace")
    monkeypatch.setenv("LIARA_WSL_SANDBOX_WINDOWS_ROOT", str(tmp_path))

    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "index.html").write_text("<html></html>", encoding="utf-8")

    tool = ListFilesTool()
    result = await tool.execute(sandbox_root="/home/liara/workspace/frontend")

    assert result["status"] == "success"
    assert result["output"]["entries"][0]["path"] == "index.html"


@pytest.mark.asyncio
async def test_list_files_rejects_invalid_entry_type():
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        tool = ListFilesTool(allowed_root=root)
        result = await tool.execute(entry_type="weird")

    assert result["status"] == "failed"
    assert "Invalid entry_type" in result["error"]


# ---------------------------------------------------------------------------
# Registry contains all built-in tools
# ---------------------------------------------------------------------------

def test_registry_has_sys_tool():
    registry = get_tool_registry()
    tools = registry.list_tools()
    assert "sys" in tools
    assert len(tools) >= 1
