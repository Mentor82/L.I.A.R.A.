"""Shared helpers for filesystem sandbox resolution and path validation."""

from __future__ import annotations

import os
import posixpath
from pathlib import Path
from pathlib import PurePosixPath


def get_sandbox_mode() -> str:
    """Return the configured sandbox mode.

    `wsl` means LIARA treats the sandbox root as a canonical WSL path and maps it
    to a local Windows-accessible path for API-side filesystem access.
    """

    configured = os.environ.get("LIARA_SANDBOX_MODE")
    if configured:
        mode = configured.strip().lower()
        if mode in {"local", "wsl"}:
            return mode
    # On Windows, default to local mode for reliability.
    # WSL sandboxing remains available via LIARA_SANDBOX_MODE=wsl.
    return "local"


def get_wsl_distro() -> str:
    return os.environ.get("LIARA_WSL_DISTRO", "Debian").strip() or "Debian"


def is_wsl_sandbox_enabled() -> bool:
    return get_sandbox_mode() == "wsl"


def get_global_canonical_sandbox_root() -> str:
    if is_wsl_sandbox_enabled():
        return _normalize_posix_absolute(
            os.environ.get("LIARA_WSL_SANDBOX_ROOT", "/home/liara/workspace")
        )
    return str(Path(os.environ.get("LIARA_READ_ROOT", Path.cwd())).resolve())


def get_global_sandbox_root() -> Path:
    """Return the global filesystem boundary for LIARA file tools."""
    if is_wsl_sandbox_enabled():
        explicit_local_root = os.environ.get("LIARA_WSL_SANDBOX_WINDOWS_ROOT", "").strip()
        if explicit_local_root:
            return Path(explicit_local_root).resolve()
        try:
            return wsl_path_to_windows_path(get_global_canonical_sandbox_root())
        except OSError:
            # Fall back to a local Windows path when the WSL UNC mount is
            # temporarily unavailable (common after WSL restart/network glitches).
            return Path(os.environ.get("LIARA_READ_ROOT", Path.cwd())).resolve()
    return Path(os.environ.get("LIARA_READ_ROOT", Path.cwd())).resolve()


def wsl_path_to_windows_path(path: str, distro: str | None = None) -> Path:
    normalized = _normalize_posix_absolute(path)
    parts = PurePosixPath(normalized).parts[1:]
    unc = "\\\\wsl$\\" + (distro or get_wsl_distro())
    if parts:
        unc += "\\" + "\\".join(parts)
    try:
        return Path(unc).resolve()
    except OSError:
        fallback = os.environ.get("LIARA_WSL_SANDBOX_WINDOWS_ROOT", "").strip()
        if fallback:
            return Path(fallback).resolve()
        return Path(os.environ.get("LIARA_READ_ROOT", Path.cwd())).resolve()


def canonicalize_sandbox_root(candidate: str | None, global_root: str | None = None) -> str:
    """Return the canonical sandbox path representation for the active mode."""

    if not is_wsl_sandbox_enabled():
        boundary = Path(global_root or get_global_sandbox_root()).resolve()
        if not candidate:
            return str(boundary)

        raw_path = Path(candidate)
        resolved = (raw_path if raw_path.is_absolute() else (boundary / raw_path)).resolve()
        ensure_within_boundary(resolved, boundary, message="Sandbox root escapes workspace boundary.")
        return str(resolved)

    boundary = PurePosixPath(_normalize_posix_absolute(global_root or get_global_canonical_sandbox_root()))
    if not candidate:
        return str(boundary)

    if _looks_like_windows_path(candidate):
        local_boundary = get_global_sandbox_root()
        target = Path(candidate).resolve()
        ensure_within_boundary(target, local_boundary, message="Sandbox root escapes workspace boundary.")
        relative = target.relative_to(local_boundary)
        return str((boundary / PurePosixPath(*relative.parts)))

    if _looks_like_wsl_absolute_path(candidate):
        resolved = PurePosixPath(_normalize_posix_absolute(candidate))
    else:
        resolved = PurePosixPath(_normalize_posix_absolute(str(boundary / candidate)))

    _ensure_within_posix_boundary(resolved, boundary, message="Sandbox root escapes workspace boundary.")
    return str(resolved)


def ensure_within_boundary(target: Path, boundary: Path, message: str) -> None:
    """Raise ValueError when target is outside the configured boundary."""
    try:
        target.resolve().relative_to(boundary.resolve())
    except ValueError as exc:
        raise ValueError(message) from exc


def resolve_sandbox_root(candidate: str | None, global_root: Path | None = None) -> Path:
    """Resolve a session sandbox root and keep it inside the global boundary."""
    if is_wsl_sandbox_enabled():
        canonical_root = PurePosixPath(canonicalize_sandbox_root(candidate))
        canonical_boundary = PurePosixPath(get_global_canonical_sandbox_root())
        _ensure_within_posix_boundary(canonical_root, canonical_boundary, message="Sandbox root escapes workspace boundary.")

        explicit_local_root = os.environ.get("LIARA_WSL_SANDBOX_WINDOWS_ROOT", "").strip()
        if explicit_local_root:
            local_boundary = Path(explicit_local_root).resolve()
            relative = canonical_root.relative_to(canonical_boundary)
            return (local_boundary / Path(*relative.parts)).resolve()

        return wsl_path_to_windows_path(str(canonical_root))

    boundary = (global_root or get_global_sandbox_root()).resolve()
    if not candidate:
        return boundary

    raw_path = Path(candidate)
    resolved = (raw_path if raw_path.is_absolute() else (boundary / raw_path)).resolve()
    ensure_within_boundary(resolved, boundary, message="Sandbox root escapes workspace boundary.")
    return resolved


def _normalize_posix_absolute(path: str) -> str:
    normalized = posixpath.normpath(path.replace("\\", "/"))
    if not normalized.startswith("/"):
        normalized = "/" + normalized.lstrip("/")
    return normalized


def _looks_like_windows_path(value: str | Any) -> bool:
    stripped = str(value or "").strip()
    return stripped.startswith("\\\\") or (len(stripped) >= 3 and stripped[1:3] == ":\\") or (len(stripped) >= 3 and stripped[1:3] == ":/")


def _looks_like_wsl_absolute_path(value: str | Any) -> bool:
    return str(value or "").strip().startswith("/")


def _ensure_within_posix_boundary(target: PurePosixPath, boundary: PurePosixPath, message: str) -> None:
    try:
        target.relative_to(boundary)
    except ValueError as exc:
        raise ValueError(message) from exc