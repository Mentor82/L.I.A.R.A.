"""Execution-backend contract for ai-validator jobs.

The validator job lifecycle and its result contract are independent from the
runtime used to execute a validator.  Docker Compose is the first concrete
backend; VM, remote-worker, or alternative container backends can register the
same protocol without changing the memory-service API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class ValidatorExecutionRequest:
    job_id: str
    workspace: str
    prepared_workspace: str
    workspace_preparation: dict[str, Any]
    scope: str
    checks: list[str] = field(default_factory=list)
    strict_mode: bool = False
    artifacts_dir: str = ""
    session_id: str | None = None


@runtime_checkable
class ValidatorExecutionBackend(Protocol):
    """Runtime-neutral adapter implemented by each validator executor."""

    name: str

    def execute(self, request: ValidatorExecutionRequest) -> dict[str, Any]:
        """Execute one validator job and return the canonical result shape."""


_BACKENDS: dict[str, ValidatorExecutionBackend] = {}


def register_validator_execution_backend(
    backend: ValidatorExecutionBackend,
    *,
    replace: bool = False,
) -> None:
    name = str(getattr(backend, "name", "")).strip().lower()
    if not name:
        raise ValueError("validator execution backend must define a name")
    if name in _BACKENDS and not replace:
        raise ValueError(f"validator execution backend already registered: {name}")
    _BACKENDS[name] = backend


def get_validator_execution_backend(name: str) -> ValidatorExecutionBackend:
    normalized = str(name or "").strip().lower()
    try:
        return _BACKENDS[normalized]
    except KeyError as exc:
        available = ", ".join(sorted(_BACKENDS)) or "none"
        raise ValueError(
            f"validator execution backend is not registered: {normalized or '<empty>'}; "
            f"available: {available}"
        ) from exc


def list_validator_execution_backends() -> tuple[str, ...]:
    return tuple(sorted(_BACKENDS))
