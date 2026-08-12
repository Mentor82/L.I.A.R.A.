"""SimulationRunner — high-level interface over JuliaBridge.

Validates inputs before dispatch and enriches the response with
provenance metadata (model, elapsed_ms, input echo).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .bridge import JuliaBridge, JuliaBridgeError

logger = logging.getLogger(__name__)

# Per-model input schemas (required keys).  Extend as models are added.
_REQUIRED_INPUTS: dict[str, list[str]] = {
    "turbine_power": ["shaft_speed_rpm", "torque_nm"],
}

# Per-model output validation (expected keys in the JSON result).
_EXPECTED_OUTPUTS: dict[str, list[str]] = {
    "turbine_power": ["power_kw"],
}


class SimulationRunner:
    """Validate → dispatch → enrich the result of a Julia simulation."""

    def __init__(self, bridge: JuliaBridge | None = None):
        self.bridge = bridge or JuliaBridge()

    async def run(
        self,
        model: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a simulation model and return an enriched result dict.

        Returns:
            ``{"status": "success", "model": ..., "inputs": ...,
               "outputs": ..., "elapsed_ms": ...}``

            or on error:
            ``{"status": "error", "model": ..., "error": ...}``
        """
        # Input validation
        required = _REQUIRED_INPUTS.get(model, [])
        missing = [k for k in required if k not in inputs]
        if missing:
            return {
                "status": "error",
                "model": model,
                "error": f"Missing required inputs: {missing}",
            }

        started = time.perf_counter()
        try:
            outputs = await self.bridge.run(model, inputs)
        except JuliaBridgeError as exc:
            logger.warning("[simulation-runner] model=%s error=%s", model, exc)
            return {"status": "error", "model": model, "error": str(exc)}

        elapsed_ms = (time.perf_counter() - started) * 1000

        # Output validation (warn only, don't fail)
        expected = _EXPECTED_OUTPUTS.get(model, [])
        missing_out = [k for k in expected if k not in outputs]
        if missing_out:
            logger.warning(
                "[simulation-runner] model=%s missing expected outputs: %s",
                model, missing_out,
            )

        return {
            "status": "success",
            "model": model,
            "inputs": inputs,
            "outputs": outputs,
            "elapsed_ms": round(elapsed_ms, 2),
        }

    def list_models(self) -> list[dict[str, Any]]:
        """Delegate to bridge — returns allowlist with presence flag."""
        return self.bridge.list_available()
