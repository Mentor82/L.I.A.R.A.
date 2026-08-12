"""Mock result generator for Safe Simulation Mode.

Generates realistic but safe mock results for simulated tool execution.
This allows the orchestrator to plan, route tools, and validate responses
without actually executing any commands or tools.
"""

from __future__ import annotations

import json
import hashlib
import time
from datetime import datetime, timezone
from typing import Any, Dict
from dataclasses import dataclass


@dataclass
class MockResult:
    """Represents a simulated tool execution result."""
    tool_name: str
    status: str = "success"
    output: Any = None
    error: str | None = None
    metadata: Dict[str, Any] | None = None
    simulated: bool = True
    simulation_confidence: float = 0.95


class MockResultGenerator:
    """Generates realistic mock results for different tool types."""

    @staticmethod
    def generate_sys_result(
        command: str,
        args: list[str] | None = None,
        context: str | None = None,
    ) -> MockResult:
        """Generate mock result for sys/shell command execution."""
        args = args or []
        context_lower = (context or "").lower()

        # === Time lookup ===
        if command == "date" or context_lower == "agent_time_lookup":
            utc_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return MockResult(
                tool_name="sys",
                status="success",
                output={
                    "source": "sys",
                    "kind": "time_lookup",
                    "utc_iso": utc_now,
                    "summary_text": f"Current UTC time: {utc_now}",
                },
                metadata={
                    "latency_ms": 12,
                    "command": "date",
                    "simulated": True,
                    "simulation_type": "time_lookup",
                },
            )

        # === Ubuntu release lookup ===
        if context_lower == "agent_ubuntu_release_lookup":
            return MockResult(
                tool_name="sys",
                status="success",
                output={
                    "source": "sys",
                    "kind": "release_lookup",
                    "product": "ubuntu",
                    "version": "22.04 LTS",
                    "codename": "jammy",
                    "summary_text": "Current Ubuntu LTS release: 22.04 LTS (jammy).",
                },
                metadata={
                    "latency_ms": 45,
                    "command": "curl",
                    "simulated": True,
                    "simulation_type": "release_lookup",
                },
            )

        # === Web search/curl ===
        if command == "curl" or context_lower == "agent_web_lookup":
            query = " ".join(args) if args else "latest news"
            return MockResult(
                tool_name="sys",
                status="success",
                output={
                    "source": "sys",
                    "kind": "web_lookup",
                    "query": query,
                    "results": [
                        {
                            "title": f"Result 1: {query}",
                            "url": "https://example.com/result1",
                            "snippet": "This is a simulated search result for planning purposes.",
                        },
                        {
                            "title": f"Result 2: {query}",
                            "url": "https://example.com/result2",
                            "snippet": "Another simulated result to show search capability.",
                        },
                    ],
                    "summary_text": f"Found 2 simulated results for: {query}",
                },
                metadata={
                    "latency_ms": 78,
                    "command": "curl",
                    "simulated": True,
                    "simulation_type": "web_lookup",
                },
            )

        # === Generic shell command ===
        output_lines = [
            f"[SIMULATED] Output from: {command} {' '.join(args)}",
            f"[SIMULATED] Execution time: {time.time():.2f}",
            "[SIMULATED] This is a mock result for planning/testing purposes.",
            f"[SIMULATED] Working directory: /home/liara/workspace",
        ]
        return MockResult(
            tool_name="sys",
            status="success",
            output="\n".join(output_lines),
            metadata={
                "latency_ms": 25,
                "command": command,
                "args": args,
                "simulated": True,
                "simulation_type": "shell_command",
            },
        )

    @staticmethod
    def generate_compute_result(
        model: str,
        inputs: dict[str, Any] | None = None,
    ) -> MockResult:
        """Generate mock result for compute/simulation tool execution."""
        inputs = inputs or {}
        model_lower = (model or "").lower()

        # === Turbine power model ===
        if "turbine" in model_lower:
            shaft_speed = float(inputs.get("shaft_speed_rpm", 1500.0))
            torque = float(inputs.get("torque_nm", 200.0))

            # Simplified turbine power formula (rough simulation)
            # Power = Torque * Angular_Velocity
            # Angular velocity (rad/s) = RPM * 2π / 60
            angular_velocity = shaft_speed * 2 * 3.14159 / 60
            power_kw = (angular_velocity * torque) / 1000

            return MockResult(
                tool_name="compute.run",
                status="success",
                output={
                    "model": model,
                    "inputs": inputs,
                    "results": {
                        "shaft_speed_rpm": shaft_speed,
                        "torque_nm": torque,
                        "power_kw": round(power_kw, 3),
                        "angular_velocity_rad_s": round(angular_velocity, 3),
                    },
                    "metadata": {
                        "simulation_time": "0.042ms",
                        "convergence": "success",
                        "iterations": 3,
                    },
                },
                metadata={
                    "latency_ms": 8,
                    "model": model,
                    "simulated": True,
                    "simulation_type": "physics_model",
                },
            )

        # === Generic compute model ===
        model_hash = hashlib.md5(model.encode()).hexdigest()[:8]
        return MockResult(
            tool_name="compute.run",
            status="success",
            output={
                "model": model,
                "inputs": inputs,
                "results": {
                    "simulated_output": f"mock_result_{model_hash}",
                    "confidence": 0.87,
                },
                "metadata": {
                    "simulation_time": "12.3ms",
                    "convergence": "success",
                },
            },
            metadata={
                "latency_ms": 12,
                "model": model,
                "simulated": True,
                "simulation_type": "generic_compute",
            },
        )

    @staticmethod
    def generate_compute_generate_result(
        model_name: str,
        description: str,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        llm_provider: str | None = None,
    ) -> MockResult:
        """Generate mock result for compute.generate tool execution."""
        model_name = (model_name or "generated_model").strip() or "generated_model"
        description = (description or "Simulated model generation").strip()
        inputs = inputs or {}
        outputs = outputs or {}
        llm_provider = (llm_provider or "ll_ol_fallback").strip() or "ll_ol_fallback"

        return MockResult(
            tool_name="compute.generate",
            status="success",
            output={
                "status": "success",
                "model_name": model_name,
                "message": f"Model '{model_name}' generated and stored successfully (simulated)",
                "model_url": f"POST /compute/run with body: {{'model': '{model_name}', 'inputs': {{...}}}}",
                "metadata": {
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "description": description,
                    "inputs": inputs,
                    "outputs": outputs,
                    "llm_model": llm_provider,
                    "syntax_valid": True,
                    "version": 1,
                    "simulated": True,
                    "simulation_type": "compute_generate",
                },
            },
            metadata={
                "latency_ms": 14,
                "simulated": True,
                "simulation_type": "compute_generate",
                "model_name": model_name,
            },
        )

    @staticmethod
    def generate_file_result(
        tool_name: str,
        path: str | None = None,
        context: str = "list_files",
    ) -> MockResult:
        """Generate mock result for file operations (read/list)."""
        # === List files ===
        if "list" in tool_name.lower():
            return MockResult(
                tool_name=tool_name,
                status="success",
                output={
                    "path": path or "/home/liara/workspace",
                    "entries": [
                        {"name": "README.md", "type": "file", "size": 1024},
                        {"name": "data.json", "type": "file", "size": 2048},
                        {"name": "src", "type": "directory", "size": 4096},
                        {"name": ".gitignore", "type": "file", "size": 256},
                    ],
                    "total": 4,
                },
                metadata={
                    "latency_ms": 5,
                    "simulated": True,
                    "simulation_type": "directory_listing",
                },
            )

        # === Read file ===
        return MockResult(
            tool_name=tool_name,
            status="success",
            output={
                "path": path or "/home/liara/workspace/README.md",
                "content": "[SIMULATED FILE CONTENT]\n\n# Simulated File\n\nThis is a mock file read result for planning purposes.\n\n"
                          "Content would normally be read from the actual file system.\n",
                "size": 128,
                "encoding": "utf-8",
            },
            metadata={
                "latency_ms": 3,
                "simulated": True,
                "simulation_type": "file_read",
            },
        )

    @staticmethod
    def generate_web_search_result(
        query: str,
    ) -> MockResult:
        """Generate mock result for web search tool."""
        return MockResult(
            tool_name="web_search",
            status="success",
            output={
                "query": query,
                "results": [
                    {
                        "title": f"Simulated result for '{query}' #1",
                        "url": "https://example.com/search1",
                        "snippet": "This is a mock search result for testing and planning.",
                        "relevance": 0.92,
                    },
                    {
                        "title": f"Simulated result for '{query}' #2",
                        "url": "https://example.com/search2",
                        "snippet": "Another simulated result to demonstrate tool capability.",
                        "relevance": 0.85,
                    },
                ],
                "total_results": 2,
            },
            metadata={
                "latency_ms": 42,
                "simulated": True,
                "simulation_type": "web_search",
            },
        )

    @staticmethod
    def generate_error_result(
        tool_name: str,
        error_code: str = "SIMULATED_ERROR",
        message: str = "Simulated tool execution (not actually executed)",
    ) -> MockResult:
        """Generate a safe error result for tools that can't be simulated."""
        return MockResult(
            tool_name=tool_name,
            status="simulated_error",
            output=None,
            error=f"[{error_code}] {message} - Tool simulation not available for this action.",
            metadata={
                "latency_ms": 0,
                "simulated": True,
                "simulation_type": "unsupported_simulation",
            },
        )

    @classmethod
    def generate(
        cls,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
    ) -> MockResult:
        """
        Unified mock result generator.

        Routes to specific generators based on tool_name and parameters.

        Args:
            tool_name: Name of the tool being simulated
            parameters: Tool parameters (e.g., {"command": "ls", "args": ["-la"]})

        Returns:
            MockResult ready for orchestrator consumption
        """
        parameters = parameters or {}

        # === System commands ===
        if tool_name == "sys":
            return cls.generate_sys_result(
                command=parameters.get("command", ""),
                args=parameters.get("args"),
                context=parameters.get("context"),
            )

        # === Compute/simulation ===
        if tool_name in {"compute.run", "compute/run"}:
            return cls.generate_compute_result(
                model=parameters.get("model", ""),
                inputs=parameters.get("inputs"),
            )

        if tool_name in {"compute.generate", "compute/generate"}:
            return cls.generate_compute_generate_result(
                model_name=parameters.get("model_name", ""),
                description=parameters.get("description", ""),
                inputs=parameters.get("inputs"),
                outputs=parameters.get("outputs"),
                llm_provider=parameters.get("llm_provider"),
            )

        # === File operations ===
        if tool_name in {"read_file", "list_files"}:
            return cls.generate_file_result(
                tool_name=tool_name,
                path=parameters.get("path"),
                context=tool_name,
            )

        # === Web search ===
        if tool_name in {"web_search", "web/search"}:
            return cls.generate_web_search_result(
                query=parameters.get("query", ""),
            )

        # === Default: unsupported tool ===
        return cls.generate_error_result(
            tool_name=tool_name,
            message=f"No simulation profile for tool '{tool_name}'",
        )
