"""Built-in tool: compute.run — delegates to Julia via SimulationRunner."""

from __future__ import annotations

from typing import Any

from services.simulation.runner import SimulationRunner

from ..base import Tool


class ComputeTool(Tool):
    """Run a deterministic Julia computation model."""

    def __init__(self) -> None:
        super().__init__()
        self._runner = SimulationRunner()

    @property
    def name(self) -> str:
        return "compute.run"

    @property
    def description(self) -> str:
        return (
            "Run a deterministic Julia simulation model. "
            "Returns computed outputs as structured JSON. "
            "Available models: turbine_power (shaft_speed_rpm, torque_nm → power_kw)."
        )

    @property
    def required_parameters(self) -> list[str]:
        return ["model", "inputs"]

    @property
    def optional_parameters(self) -> list[str]:
        return []

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        model: str = kwargs.get("model", "")
        inputs: dict[str, Any] = kwargs.get("inputs", {})

        if not model:
            return self.failure("'model' parameter is required")
        if not isinstance(inputs, dict):
            return self.failure("'inputs' must be a JSON object")

        result = await self._runner.run(model, inputs)

        if result.get("status") == "success":
            return self.success(result)
        return self.failure(result.get("error", "simulation failed"))
