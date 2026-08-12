"""Unit tests for JuliaBridge and SimulationRunner."""

from __future__ import annotations

import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.simulation.bridge import JuliaBridge, JuliaBridgeError
from services.simulation.runner import SimulationRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bridge(tmp_path: pathlib.Path, allowlist: list[str] | None = None) -> JuliaBridge:
    return JuliaBridge(
        julia_exe="julia",
        models_dir=tmp_path,
        allowlist=allowlist or ["turbine_power"],
        timeout_seconds=10.0,
        mode="local",
        executor=MagicMock(),
    )


def _write_model(tmp_path: pathlib.Path, name: str, content: str) -> pathlib.Path:
    path = tmp_path / f"{name}.jl"
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# JuliaBridge — allowlist enforcement
# ---------------------------------------------------------------------------

class TestJuliaBridgeAllowlist:
    def test_rejects_unlisted_model(self, tmp_path):
        bridge = _make_bridge(tmp_path, allowlist=["turbine_power"])
        with pytest.raises(JuliaBridgeError, match="not in the Julia allowlist"):
            bridge._resolve_script("evil_script")

    def test_accepts_model_with_jl_suffix(self, tmp_path):
        _write_model(tmp_path, "turbine_power", "")
        bridge = _make_bridge(tmp_path)
        path = bridge._resolve_script("turbine_power.jl")
        assert path.name == "turbine_power.jl"

    def test_rejects_model_file_not_found(self, tmp_path):
        bridge = _make_bridge(tmp_path, allowlist=["turbine_power"])
        # File not written → should raise
        with pytest.raises(JuliaBridgeError, match="not found"):
            bridge._resolve_script("turbine_power")

    def test_list_available_shows_present_flag(self, tmp_path):
        _write_model(tmp_path, "turbine_power", "")
        bridge = JuliaBridge(
            julia_exe="julia",
            models_dir=tmp_path,
            allowlist=["turbine_power", "pid_controller"],
            mode="local",
        )
        available = bridge.list_available()
        names = {m["name"]: m["present"] for m in available}
        assert names["turbine_power"] is True
        assert names["pid_controller"] is False


# ---------------------------------------------------------------------------
# JuliaBridge — WSL sys integration
# ---------------------------------------------------------------------------

class TestJuliaBridgeExecution:
    def _mock_executor(self, *, stdout: str = '{"power_kw": 31.4159}', stderr: str = "") -> MagicMock:
        executor = MagicMock()
        executor.execute = AsyncMock(
            side_effect=[
                {"status": "success", "output": "", "metadata": {}},
                {"status": "success", "output": "", "metadata": {}},
                {"status": "success", "output": stdout, "metadata": {"stderr": stderr}},
            ]
        )
        return executor

    @pytest.mark.asyncio
    async def test_turbine_power_basic(self, tmp_path):
        _write_model(tmp_path, "turbine_power", "print(1)")
        executor = self._mock_executor()
        bridge = JuliaBridge(
            julia_exe="julia",
            models_dir=tmp_path,
            allowlist=["turbine_power"],
            timeout_seconds=30.0,
            mode="wsl",
            executor=executor,
        )
        result = await bridge.run("turbine_power", {"shaft_speed_rpm": 1500.0, "torque_nm": 200.0})
        assert "power_kw" in result
        assert abs(result["power_kw"] - 31.4159) < 0.01
        staged_call = executor.execute.await_args_list[2]
        assert staged_call.kwargs["command"] == "julia"
        assert staged_call.kwargs["args"][-1] == "/home/liara/temp/liara-models/turbine_power.jl"

    @pytest.mark.asyncio
    async def test_turbine_power_zero_torque(self, tmp_path):
        _write_model(tmp_path, "turbine_power", "print(1)")
        executor = self._mock_executor(stdout='{"power_kw": 0.0, "power_w": 0.0}')
        bridge = JuliaBridge(
            julia_exe="julia",
            models_dir=tmp_path,
            allowlist=["turbine_power"],
            timeout_seconds=30.0,
            mode="wsl",
            executor=executor,
        )
        result = await bridge.run("turbine_power", {"shaft_speed_rpm": 3000.0, "torque_nm": 0.0})
        assert result["power_kw"] == 0.0
        assert result["power_w"] == 0.0

    @pytest.mark.asyncio
    async def test_stage_failure_raises_bridge_error(self, tmp_path):
        _write_model(tmp_path, "turbine_power", "print(1)")
        executor = MagicMock()
        executor.execute = AsyncMock(
            side_effect=[
                {"status": "success", "output": "", "metadata": {}},
                {"status": "failed", "error": "tee blocked", "metadata": {}},
            ]
        )
        bridge = JuliaBridge(models_dir=tmp_path, allowlist=["turbine_power"], mode="wsl", executor=executor)
        with pytest.raises(JuliaBridgeError, match="Failed to stage"):
            await bridge.run("turbine_power", {"shaft_speed_rpm": 1500.0, "torque_nm": 200.0})


# ---------------------------------------------------------------------------
# SimulationRunner — validation logic (mocked bridge)
# ---------------------------------------------------------------------------

class TestSimulationRunnerValidation:
    def _runner_with_fake_bridge(self, fake_output: dict) -> SimulationRunner:
        bridge = MagicMock()
        bridge.run = AsyncMock(return_value=fake_output)
        return SimulationRunner(bridge=bridge)

    @pytest.mark.asyncio
    async def test_missing_required_input_returns_error(self):
        runner = SimulationRunner(bridge=MagicMock())
        result = await runner.run("turbine_power", {"shaft_speed_rpm": 1500.0})
        assert result["status"] == "error"
        assert "torque_nm" in result["error"]

    @pytest.mark.asyncio
    async def test_successful_run_returns_enriched_result(self):
        runner = self._runner_with_fake_bridge({"power_kw": 31.4, "power_w": 31400.0, "angular_velocity": 157.08})
        result = await runner.run("turbine_power", {"shaft_speed_rpm": 1500.0, "torque_nm": 200.0})
        assert result["status"] == "success"
        assert result["model"] == "turbine_power"
        assert result["outputs"]["power_kw"] == 31.4
        assert "elapsed_ms" in result
        assert result["inputs"]["shaft_speed_rpm"] == 1500.0

    @pytest.mark.asyncio
    async def test_bridge_error_propagates_as_error_status(self):
        bridge = MagicMock()
        bridge.run = AsyncMock(side_effect=JuliaBridgeError("Julia timed out"))
        runner = SimulationRunner(bridge=bridge)
        result = await runner.run("turbine_power", {"shaft_speed_rpm": 1500.0, "torque_nm": 200.0})
        assert result["status"] == "error"
        assert "timed out" in result["error"]

    def test_list_models_delegates_to_bridge(self):
        bridge = MagicMock()
        bridge.list_available.return_value = [{"name": "turbine_power", "present": True}]
        runner = SimulationRunner(bridge=bridge)
        models = runner.list_models()
        assert models[0]["name"] == "turbine_power"
