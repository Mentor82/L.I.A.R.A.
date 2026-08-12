"""Built-in: Read a local file from the allowed workspace directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from services.shared.sandboxing import ensure_within_boundary, get_global_sandbox_root, resolve_sandbox_root

from ..base import Tool


_MAX_BYTES = 256_000  # 256 KB safety cap


class ReadFileTool(Tool):
    """Read a local file from the allowed workspace directory."""

    def __init__(self, allowed_root: Path | None = None) -> None:
        self._root = (allowed_root or get_global_sandbox_root()).resolve()

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a local file within the allowed workspace directory"

    @property
    def required_parameters(self) -> list[str]:
        return ["path"]

    @property
    def optional_parameters(self) -> list[str]:
        return ["encoding", "sandbox_root", "session_id"]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self._validate_parameters(**kwargs)

        rel_path: str = kwargs["path"]
        encoding: str = kwargs.get("encoding", "utf-8")
        sandbox_root = kwargs.get("sandbox_root")

        try:
            effective_root = resolve_sandbox_root(sandbox_root, self._root)
        except ValueError as exc:
            return self.failure(str(exc))

        # Resolve and jail-check — prevent path traversal
        try:
            target = (effective_root / rel_path).resolve()
            ensure_within_boundary(target, effective_root, "Access denied: path escapes workspace boundary.")
        except Exception as e:
            return self.failure(f"Invalid path: {e}")

        if not target.exists():
            return self.failure(f"File not found: {rel_path}")
        if not target.is_file():
            return self.failure(f"Not a file: {rel_path}")

        try:
            file_size = target.stat().st_size
            raw = target.read_bytes()[:_MAX_BYTES]
            content = raw.decode(encoding, errors="replace")
        except Exception as e:
            return self.failure(str(e))

        return self.success(
            {"path": str(target.relative_to(effective_root)), "content": content, "size": len(raw)},
            {
                "encoding": encoding,
                "truncated": file_size > _MAX_BYTES,
                "sandbox_root": str(effective_root),
            },
        )
