"""Built-in: List files from the allowed workspace directory."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from services.shared.sandboxing import ensure_within_boundary, get_global_sandbox_root, resolve_sandbox_root

from ..base import Tool


class ListFilesTool(Tool):
    """List files and folders within the allowed workspace directory."""

    def __init__(self, allowed_root: Path | None = None) -> None:
        self._root = (allowed_root or get_global_sandbox_root()).resolve()

    @property
    def name(self) -> str:
        return "list_files"

    @property
    def description(self) -> str:
        return "List files and folders within the allowed workspace directory"

    @property
    def required_parameters(self) -> list[str]:
        return []

    @property
    def optional_parameters(self) -> list[str]:
        return ["path", "recursive", "max_entries", "pattern", "entry_type", "sandbox_root", "session_id"]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self._validate_parameters(**kwargs)

        rel_path = kwargs.get("path", ".")
        recursive = bool(kwargs.get("recursive", False))
        max_entries = int(kwargs.get("max_entries", 200))
        max_entries = min(max(max_entries, 1), 1000)
        pattern = str(kwargs.get("pattern", "*")).strip() or "*"
        entry_type = str(kwargs.get("entry_type", "all")).strip().lower()
        sandbox_root = kwargs.get("sandbox_root")

        if entry_type not in {"all", "file", "directory"}:
            return self.failure("Invalid entry_type. Use 'all', 'file', or 'directory'.")

        try:
            effective_root = resolve_sandbox_root(sandbox_root, self._root)
        except ValueError as exc:
            return self.failure(str(exc))

        try:
            target = (effective_root / rel_path).resolve()
            ensure_within_boundary(target, effective_root, "Access denied: path escapes workspace boundary.")
        except Exception as exc:
            return self.failure(f"Invalid path: {exc}")
        if not target.exists():
            return self.failure(f"Path not found: {rel_path}")
        if not target.is_dir():
            return self.failure(f"Not a directory: {rel_path}")

        entries: list[dict[str, Any]] = []
        try:
            iterator = target.rglob("*") if recursive else target.iterdir()
            for item in iterator:
                item_type = "directory" if item.is_dir() else "file"
                relative = item.relative_to(effective_root).as_posix()

                if entry_type != "all" and item_type != entry_type:
                    continue
                if not (
                    fnmatch.fnmatch(item.name, pattern)
                    or fnmatch.fnmatch(relative, pattern)
                ):
                    continue

                if len(entries) >= max_entries:
                    break
                entries.append(
                    {
                        "path": relative,
                        "type": item_type,
                    }
                )
        except Exception as exc:
            return self.failure(str(exc))

        return self.success(
            {
                "root": str(effective_root),
                "path": str(target.relative_to(effective_root).as_posix()) if target != effective_root else ".",
                "recursive": recursive,
                "pattern": pattern,
                "entry_type": entry_type,
                "count": len(entries),
                "entries": entries,
            },
            {"max_entries": max_entries},
        )