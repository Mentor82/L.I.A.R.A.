"""JuliaBridge — controlled runner for Julia simulation models.

Security model:
- Only allowlisted .jl files from JULIA_MODELS_DIR may be executed.
- No arbitrary code execution; input/output strictly via JSON on stdin/stdout.
- Default runtime path is the policy-gated Debian WSL executor.
- Host-local execution is an explicit override via JULIA_BRIDGE_MODE=local.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import asyncio
from typing import Any
from typing import TYPE_CHECKING

from services.config import Settings

if TYPE_CHECKING:
    from services.tools.builtin.wsl_executor import WslExecutorTool

logger = logging.getLogger(__name__)


class JuliaBridgeError(RuntimeError):
    pass


class JuliaBridge:
    """Run an allowlisted Julia model script through Debian WSL by default.

    The Julia script must:
    - read a JSON object from stdin (the input payload)
    - write a JSON object to stdout (the result)
    - exit 0 on success, non-zero on error
    """

    def __init__(
        self,
        *,
        julia_exe: str | None = None,
        models_dir: str | pathlib.Path | None = None,
        allowlist: list[str] | None = None,
        timeout_seconds: float | None = None,
        mode: str | None = None,
        executor: "WslExecutorTool" | None = None,
        wsl_models_dir: str = "/home/liara/temp/liara-models",
    ):
        self.julia_exe = julia_exe or Settings.JULIA_EXECUTABLE
        self.models_dir = pathlib.Path(models_dir or Settings.JULIA_MODELS_DIR)
        self.allowlist: list[str] = (
            allowlist if allowlist is not None else Settings.julia_allowlist()
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else Settings.JULIA_TIMEOUT_SECONDS
        )
        raw_mode = (mode or Settings.JULIA_BRIDGE_MODE).strip().lower()
        self.mode = "wsl" if raw_mode == "wsl" else "local"

        if self.mode == "local":
            # Guard against stale WSL-style paths from older env files.
            # In local mode, prefer a host-local Julia executable.
            if self.julia_exe.startswith("/"):
                self.julia_exe = os.getenv("JULIA_EXECUTABLE_LOCAL", "julia")

        if self.mode == "wsl" and executor is None:
            from services.tools.builtin.wsl_executor import WslExecutorTool

            executor = WslExecutorTool()
        self.executor = executor
        self.wsl_models_dir = pathlib.PurePosixPath(wsl_models_dir)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, model_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Execute a Julia model and return its JSON output.

        Args:
            model_name: Basename of the .jl file (e.g. ``"turbine_power"``).
                        The ``.jl`` suffix is added automatically if omitted.
            payload: JSON-serialisable input dict, sent to Julia via stdin.

        Returns:
            Parsed JSON output from the Julia script.

        Raises:
            JuliaBridgeError: if the model is not allowlisted, not found,
                              times out, or exits non-zero.
        """
        key = model_name.removesuffix(".jl")
        script_path = self._resolve_script(model_name)
        input_json = json.dumps(payload)

        if self.mode == "wsl":
            logger.info("[julia-bridge] Running model=%s via WSL sys timeout=%.1fs", model_name, self.timeout_seconds)
            staged_path = await self._stage_script(key=key, script_path=script_path)
            if self.executor is None:
                raise JuliaBridgeError("WSL mode requires a WSL executor, but none is configured.")
            execution = await self.executor.execute(
                command=self.julia_exe,
                args=["--startup-file=no", "--quiet", str(staged_path)],
                stdin_text=input_json,
                timeout=int(max(1, round(self.timeout_seconds))),
            )

            if execution.get("status") != "success":
                error = execution.get("error", "Julia execution failed")
                stderr_text = str(execution.get("metadata", {}).get("stderr", "")).strip()
                raise JuliaBridgeError(
                    f"Julia model '{model_name}' failed via WSL sys: {error}. "
                    f"stderr: {stderr_text or '(empty)'}"
                )

            stdout_text = str(execution.get("output", "")).strip()
            stderr_text = str(execution.get("metadata", {}).get("stderr", "")).strip()
        else:
            logger.info("[julia-bridge] Running model=%s via local Julia timeout=%.1fs", model_name, self.timeout_seconds)
            try:
                process = await asyncio.create_subprocess_exec(
                    self.julia_exe,
                    "--startup-file=no",
                    "--quiet",
                    str(script_path),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            except FileNotFoundError as exc:
                raise JuliaBridgeError(
                    f"Julia executable not found: '{self.julia_exe}'. "
                    "Set JULIA_EXECUTABLE or install Julia."
                ) from exc
            except NotImplementedError as exc:
                raise JuliaBridgeError(
                    "Local Julia execution is not supported by the current event loop. "
                    "Use JULIA_BRIDGE_MODE=wsl or rely on the Python fallback."
                ) from exc

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(input_json.encode("utf-8")),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise JuliaBridgeError(
                    f"Julia model '{model_name}' timed out after {self.timeout_seconds:.1f}s"
                ) from exc

            stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()

            if process.returncode != 0:
                raise JuliaBridgeError(
                    f"Julia model '{model_name}' failed with exit code {process.returncode}. "
                    f"stderr: {stderr_text or '(empty)'}"
                )

        if not stdout_text:
            raise JuliaBridgeError(
                f"Julia model '{model_name}' produced no output. "
                f"stderr: {stderr_text or '(empty)'}"
            )

        try:
            result = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise JuliaBridgeError(
                f"Julia model '{model_name}' returned non-JSON: {stdout_text[:200]}"
            ) from exc

        if stderr_text:
            logger.debug("[julia-bridge] model=%s stderr: %s", model_name, stderr_text)

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_script(self, model_name: str) -> pathlib.Path:
        """Validate allowlist membership and return the absolute script path."""
        # Normalise: strip optional .jl suffix for allowlist check
        key = model_name.removesuffix(".jl")
        if key not in self.allowlist:
            raise JuliaBridgeError(
                f"Model '{model_name}' is not in the Julia allowlist. "
                f"Allowed: {sorted(self.allowlist)}"
            )
        script_path = self.models_dir / f"{key}.jl"
        if not script_path.exists():
            raise JuliaBridgeError(
                f"Julia model script not found: {script_path}"
            )
        return script_path

    async def _stage_script(
        self,
        *,
        key: str,
        script_path: pathlib.Path,
    ) -> pathlib.PurePosixPath:
        mkdir_result = await self.executor.execute(
            command="mkdir",
            args=["-p", str(self.wsl_models_dir)],
        )
        if mkdir_result.get("status") != "success":
            raise JuliaBridgeError(
                f"Failed to prepare WSL Julia models dir: {mkdir_result.get('error', 'mkdir failed')}"
            )

        staged_path = self.wsl_models_dir / f"{key}.jl"
        write_result = await self.executor.execute(
            command="tee",
            args=[str(staged_path)],
            stdin_text=script_path.read_text(encoding="utf-8"),
        )
        if write_result.get("status") != "success":
            raise JuliaBridgeError(
                f"Failed to stage Julia model '{key}' into WSL: {write_result.get('error', 'tee failed')}"
            )

        return staged_path

    def list_available(self) -> list[dict[str, Any]]:
        """List models that are both allowlisted and physically present."""
        result = []
        for name in sorted(self.allowlist):
            path = self.models_dir / f"{name}.jl"
            result.append({"name": name, "path": str(path), "present": path.exists()})
        return result
