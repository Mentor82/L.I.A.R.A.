"""Canonical artifact storage backed by LIARA's verified WSL SYS path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any


_DEFAULT_WSL_ROOT = "/home/liara/workspace"
_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_filename_token(value: str, *, fallback: str, limit: int = 64) -> str:
    token = _TOKEN_RE.sub("-", str(value or "").strip()).strip(".-")
    return (token or fallback)[:limit]


class ArtifactStore:
    """Write artifacts through SYS and read them through the same WSL filesystem."""

    def __init__(
        self,
        *,
        mode: str,
        canonical_root: str,
        local_root: Path,
        distro: str,
        user: str,
    ) -> None:
        if mode not in {"local", "wsl"}:
            raise ValueError("artifact store mode must be local or wsl")
        normalized_root = PurePosixPath(canonical_root)
        if not normalized_root.is_absolute():
            raise ValueError("artifact WSL root must be absolute")
        self.mode = mode
        self.canonical_root = normalized_root
        self.local_root = Path(local_root)
        self.distro = distro
        self.user = user

    @property
    def canonical_artifacts_root(self) -> PurePosixPath:
        return self.canonical_root / ".liara_artifacts"

    @property
    def local_artifacts_root(self) -> Path:
        return self.local_root / ".liara_artifacts"

    def canonical_path(self, artifact_dir: str, filename: str) -> PurePosixPath:
        safe_dir = safe_filename_token(artifact_dir, fallback="other")
        safe_name = safe_filename_token(filename, fallback="artifact.json", limit=160)
        target = self.canonical_artifacts_root / safe_dir / safe_name
        target.relative_to(self.canonical_artifacts_root)
        return target

    def local_path(self, artifact_dir: str, filename: str) -> Path:
        safe_dir = safe_filename_token(artifact_dir, fallback="other")
        safe_name = safe_filename_token(filename, fallback="artifact.json", limit=160)
        target = (self.local_artifacts_root / safe_dir / safe_name).resolve()
        target.relative_to(self.local_artifacts_root.resolve())
        return target

    def read_directory(self, artifact_dir: str) -> Path:
        safe_dir = safe_filename_token(artifact_dir, fallback="other")
        return self.local_artifacts_root / safe_dir

    def write_json(
        self,
        *,
        artifact_dir: str,
        filename: str,
        payload: dict[str, Any],
        request_id: str,
        run_id: str,
        session_id: str | None,
        source: str,
    ) -> Path | PurePosixPath:
        content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        if self.mode == "local":
            return self._write_local(
                artifact_dir=artifact_dir,
                filename=filename,
                content=content,
            )
        return self._write_wsl(
            artifact_dir=artifact_dir,
            filename=filename,
            content=content,
            request_id=request_id,
            run_id=run_id,
            session_id=session_id,
            source=source,
        )

    def _write_local(self, *, artifact_dir: str, filename: str, content: str) -> Path:
        target = self.local_path(artifact_dir, filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        content_bytes = content.encode("utf-8")
        expected = hashlib.sha256(content_bytes).hexdigest()
        target.write_bytes(content_bytes)
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"local artifact verification failed for {target}")
        return target

    def _write_wsl(
        self,
        *,
        artifact_dir: str,
        filename: str,
        content: str,
        request_id: str,
        run_id: str,
        session_id: str | None,
        source: str,
    ) -> PurePosixPath:
        from services.tools.builtin.wsl_executor import WslExecutorTool

        target = self.canonical_path(artifact_dir, filename)
        target_dir = target.parent
        tool = WslExecutorTool()

        async def _persist() -> None:
            common = {
                "workdir": str(self.canonical_root),
                "request_id": request_id,
                "run_id": run_id,
                "session_id": session_id,
                "source": source,
                "context": "workspace.artifact_store",
                "storage_scope": "workspace",
            }
            mkdir_result = await tool.execute(
                command="mkdir",
                args=["-p", str(target_dir)],
                target_path=str(target_dir),
                write_mode="mkdir",
                **common,
            )
            if mkdir_result.get("status") != "success" or not (mkdir_result.get("metadata") or {}).get("mutation_verified"):
                raise RuntimeError(f"WSL artifact directory creation failed: {mkdir_result.get('error') or mkdir_result}")

            write_result = await tool.execute(
                command="tee",
                args=[str(target)],
                stdin_text=content,
                stdin_transport="threaded",
                target_path=str(target),
                write_mode="overwrite",
                **common,
            )
            evidence = (write_result.get("metadata") or {}).get("mutation_evidence") or {}
            if write_result.get("status") != "success" or not evidence.get("verified"):
                raise RuntimeError(f"WSL artifact write failed: {write_result.get('error') or write_result}")

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(_persist())
        else:
            raise RuntimeError("WSL artifact persistence must run outside the active event-loop thread")
        return target


def build_artifact_store(*, local_workspace_root: Path) -> ArtifactStore:
    configured_mode = os.getenv("LIARA_ARTIFACT_STORE_MODE", "auto").strip().lower() or "auto"
    if configured_mode not in {"auto", "local", "wsl"}:
        raise ValueError("LIARA_ARTIFACT_STORE_MODE must be auto, local, or wsl")

    canonical_root = (
        os.getenv("LIARA_ARTIFACT_WSL_ROOT")
        or os.getenv("LIARA_WSL_SANDBOX_ROOT")
        or _DEFAULT_WSL_ROOT
    ).strip()
    looks_like_posix_default = str(local_workspace_root).replace("\\", "/").rstrip("/").endswith("/home/liara/workspace")
    mode = configured_mode
    if mode == "auto":
        mode = "wsl" if os.name == "nt" and looks_like_posix_default else "local"

    distro = os.getenv("LIARA_WSL_DISTRO", "Debian").strip() or "Debian"
    user = os.getenv("LIARA_WSL_USER", "liara").strip() or "liara"
    if mode == "wsl":
        explicit_windows_root = os.getenv("LIARA_ARTIFACT_WSL_WINDOWS_ROOT", "").strip()
        if explicit_windows_root:
            local_root = Path(explicit_windows_root)
        else:
            relative = PurePosixPath(canonical_root).parts[1:]
            local_root = Path(rf"\\wsl.localhost\{distro}").joinpath(*relative)
    else:
        local_root = local_workspace_root

    return ArtifactStore(
        mode=mode,
        canonical_root=canonical_root,
        local_root=local_root,
        distro=distro,
        user=user,
    )
