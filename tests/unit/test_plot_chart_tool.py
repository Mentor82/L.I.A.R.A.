"""Unit tests for plot_chart built-in tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.tools.builtin.plot_chart import PlotChartTool
from services.tools.registry import get_tool_registry


@pytest.mark.asyncio
async def test_plot_chart_tool_generates_png_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")
    monkeypatch.setenv("LIARA_READ_ROOT", str(tmp_path))

    tool = PlotChartTool(allowed_root=tmp_path)
    result = await tool.execute(
        session_id="session-plot",
        chart_type="line",
        title="Umsatz",
        x_values=[1, 2, 3, 4],
        y_values=[10, 15, 12, 18],
    )

    assert result["status"] == "success"
    output = result["output"]
    assert output["chart_type"] == "line"
    assert output["title"] == "Umsatz"
    assert isinstance(output["artifacts"], list)
    artifact = output["artifacts"][0]
    assert artifact["kind"] == "image"
    assert artifact["mime_type"] == "image/png"
    assert artifact["source_tool"] == "plot_chart"
    rel_path = artifact["metadata"]["stored_path"]
    assert rel_path.endswith(".png")

    full_path = Path(tmp_path) / rel_path
    assert full_path.exists()
    assert full_path.stat().st_size > 0


@pytest.mark.asyncio
async def test_plot_chart_tool_rejects_mismatched_series(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_SANDBOX_MODE", "local")
    monkeypatch.setenv("LIARA_READ_ROOT", str(tmp_path))

    tool = PlotChartTool(allowed_root=tmp_path)
    result = await tool.execute(
        session_id="session-plot",
        x_values=[1, 2, 3],
        y_values=[10, 15],
    )

    assert result["status"] == "failed"
    assert "same length" in result["error"]


def test_plot_chart_tool_is_registered():
    registry = get_tool_registry()
    names = registry.list_tools()
    assert "plot_chart" in names
