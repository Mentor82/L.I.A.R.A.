"""Safe argument builder for sys commands (Idea #51: Sicheres Args-Building pro /sys Command).

This module provides structured, safe argument generation for each sys command type.
Each command has predefined safe defaults and validators that prevent unsafe combinations.

Design principles:
  1. Default-args per command are hardcoded and safe (whitelist-based)
  2. Workspace path validation ensures all paths stay in /home/liara/workspace
  3. Structured arg lists (list[str]) instead of shell strings
  4. Never generate /sys requests for unclear or unsafe cases
  5. Clear error messages for policy violations

Commands with safe arg builders:
  - curl      → URL-fetch-only (HTTP/HTTPS, no file://, no -F uploads)
  - find      → workspace-only, no -exec, no -delete
  - ls        → workspace-only, no symlink-following
  - head      → workspace-only, safe flags only
  - tail      → workspace-only, safe flags only
  - grep      → workspace-only, no -e patterns requiring bash eval
  - cat       → workspace-only, read-only
  - date      → timezone+format only, no date-setting commands
    - mkdir     → workspace/tmp-only, controlled directory creation
    - touch     → workspace/tmp-only, controlled empty-file creation
    - tee       → workspace/tmp-only, controlled file writes via stdin
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class ArgBuilderError(Exception):
    """Base exception for argument builder errors."""

    pass


class UnsafePathError(ArgBuilderError):
    """Raised when a path is outside the safe workspace."""

    pass


class UnsafeArgumentError(ArgBuilderError):
    """Raised when an argument violates policy."""

    pass


# ── Safe workspace path ────────────────────────────────────────────────────────

SAFE_WORKSPACE_ROOT = Path("/home/liara/workspace")
"""The only path prefix allowed for file operations in sys commands."""

SAFE_TMP_ROOT = Path("/home/liara/temp")
"""Temporary files are allowed but discouraged; used only for scratch data."""


class PathScope(str, Enum):
    WORKSPACE = "workspace"
    TEMP = "temp"
    ANY_MANAGED = "any_managed"


def validate_workspace_path(path_str: str) -> Path:
    """Validate that a path is within the safe workspace root.

    Args:
        path_str: Relative or absolute path string.

    Returns:
        Resolved Path object.

    Raises:
        UnsafePathError: If path is outside safe workspace.
    """
    try:
        p = Path(path_str)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            # Relative paths are resolved within workspace root
            resolved = (SAFE_WORKSPACE_ROOT / p).resolve()

        # Check that resolved path is within workspace
        if not str(resolved).startswith(str(SAFE_WORKSPACE_ROOT.resolve())):
            raise UnsafePathError(
                f"Path '{path_str}' resolves to '{resolved}' "
                f"which is outside {SAFE_WORKSPACE_ROOT}"
            )

        return resolved
    except (ValueError, OSError) as e:
        raise UnsafePathError(f"Invalid path '{path_str}': {e}") from e


def validate_managed_path(path_str: str, *, scope: PathScope = PathScope.ANY_MANAGED) -> Path:
    """Validate that a path stays within managed write roots.

    WORKSPACE: persistent files below /home/liara/workspace
    TEMP: ephemeral files below /home/liara/temp
    ANY_MANAGED: either workspace or temp
    """
    try:
        p = Path(path_str)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            base = SAFE_TMP_ROOT if scope == PathScope.TEMP else SAFE_WORKSPACE_ROOT
            resolved = (base / p).resolve()

        workspace_root = SAFE_WORKSPACE_ROOT.resolve()
        tmp_root = SAFE_TMP_ROOT.resolve()
        in_workspace = str(resolved).startswith(str(workspace_root))
        in_tmp = str(resolved).startswith(str(tmp_root))

        if scope == PathScope.WORKSPACE and not in_workspace:
            raise UnsafePathError(f"Path '{path_str}' must stay within {SAFE_WORKSPACE_ROOT}")
        if scope == PathScope.TEMP and not in_tmp:
            raise UnsafePathError(f"Path '{path_str}' must stay within {SAFE_TMP_ROOT}")
        if scope == PathScope.ANY_MANAGED and not (in_workspace or in_tmp):
            raise UnsafePathError(
                f"Path '{path_str}' resolves to '{resolved}' which is outside managed roots {SAFE_WORKSPACE_ROOT} and {SAFE_TMP_ROOT}"
            )
        return resolved
    except (ValueError, OSError) as e:
        raise UnsafePathError(f"Invalid path '{path_str}': {e}") from e


# ── Command arg builders ───────────────────────────────────────────────────────
# Each builder: (intent, kwargs) -> Args


@dataclass
class CurlArgs:
    """Safe curl argument builder."""

    url: str
    headers: dict[str, str] | None = None
    timeout: int = 30
    max_size: int = 5 * 1024 * 1024  # 5 MB cap

    def build(self) -> list[str]:
        """Build curl command args, validation-safe."""
        args = ["curl"]

        # URL validation: only http/https, no file://, no data://, etc.
        if not (self.url.startswith("http://") or self.url.startswith("https://")):
            raise UnsafeArgumentError(f"Only http:// and https:// URLs allowed, got: {self.url}")

        # Basic URL format check (no shell metacharacters)
        if re.search(r'[;&|`$(){}[\]<>\\]', self.url):
            raise UnsafeArgumentError(f"URL contains shell metacharacters: {self.url}")

        args.append(self.url)

        # Add headers (safe: no interpolation, just pass-through)
        if self.headers:
            for key, value in self.headers.items():
                if not re.match(r"^[A-Za-z0-9\-]+$", key):
                    raise UnsafeArgumentError(f"Invalid header name: {key}")
                # Header values should not contain newlines or shell chars
                if "\n" in value or "\r" in value:
                    raise UnsafeArgumentError(f"Header value contains newline: {key}")
                args.extend(["-H", f"{key}: {value}"])

        # Add timeout
        args.extend(["--max-time", str(self.timeout)])

        # Add max size limit
        args.extend(["--max-filesize", str(self.max_size)])

        return args


@dataclass
class FindArgs:
    """Safe find argument builder (workspace-only)."""

    path: str = "."
    max_depth: int | None = None
    name_pattern: str | None = None
    file_type: str | None = None  # "f", "d", "l"

    def build(self) -> list[str]:
        """Build find command args."""
        resolved_path = validate_workspace_path(self.path)
        args = ["find", str(resolved_path)]

        if self.max_depth is not None:
            if not (1 <= self.max_depth <= 10):
                raise UnsafeArgumentError(f"max_depth must be 1-10, got {self.max_depth}")
            args.extend(["-maxdepth", str(self.max_depth)])

        if self.file_type:
            if self.file_type not in ("f", "d", "l"):
                raise UnsafeArgumentError(
                    f"file_type must be 'f' (file), 'd' (dir), or 'l' (link), got {self.file_type}"
                )
            args.extend(["-type", self.file_type])

        if self.name_pattern:
            # Name pattern validation: no regex metacharacters that could cause issues
            if not re.match(r"^[a-zA-Z0-9_\-.*?[\]]+$", self.name_pattern):
                raise UnsafeArgumentError(f"name_pattern contains unsafe characters: {self.name_pattern}")
            args.extend(["-name", self.name_pattern])

        return args


@dataclass
class LsArgs:
    """Safe ls argument builder (workspace-only)."""

    path: str = "."
    long_format: bool = False
    all_files: bool = False
    recursive: bool = False

    def build(self) -> list[str]:
        """Build ls command args."""
        resolved_path = validate_workspace_path(self.path)
        args = ["ls"]

        if self.long_format:
            args.append("-l")
        if self.all_files:
            args.append("-a")
        if self.recursive:
            if not self.long_format:
                # Recursive + long format is safer
                args.append("-l")
            args.append("-R")

        args.append(str(resolved_path))
        return args


@dataclass
class GrepArgs:
    """Safe grep argument builder (workspace-only)."""

    pattern: str
    path: str = "."
    case_insensitive: bool = False
    line_numbers: bool = False
    count_only: bool = False
    invert_match: bool = False

    def build(self) -> list[str]:
        """Build grep command args."""
        resolved_path = validate_workspace_path(self.path)

        # Pattern validation: no shell metacharacters
        if re.search(r'[;<>`$(){}[\]|&\\]', self.pattern):
            raise UnsafeArgumentError(
                f"grep pattern contains shell metacharacters: {self.pattern}"
            )

        args = ["grep"]

        if self.case_insensitive:
            args.append("-i")
        if self.line_numbers:
            args.append("-n")
        if self.count_only:
            args.append("-c")
        if self.invert_match:
            args.append("-v")

        args.append(self.pattern)
        args.append(str(resolved_path))

        return args


@dataclass
class HeadArgs:
    """Safe head argument builder (workspace-only)."""

    path: str
    num_lines: int = 10

    def build(self) -> list[str]:
        """Build head command args."""
        resolved_path = validate_workspace_path(self.path)

        if not (1 <= self.num_lines <= 1000):
            raise UnsafeArgumentError(f"num_lines must be 1-1000, got {self.num_lines}")

        args = ["head", "-n", str(self.num_lines), str(resolved_path)]
        return args


@dataclass
class TailArgs:
    """Safe tail argument builder (workspace-only)."""

    path: str
    num_lines: int = 10
    follow: bool = False

    def build(self) -> list[str]:
        """Build tail command args."""
        resolved_path = validate_workspace_path(self.path)

        if not (1 <= self.num_lines <= 1000):
            raise UnsafeArgumentError(f"num_lines must be 1-1000, got {self.num_lines}")

        args = ["tail", "-n", str(self.num_lines)]

        if self.follow:
            args.append("-f")

        args.append(str(resolved_path))
        return args


@dataclass
class CatArgs:
    """Safe cat argument builder (workspace-only, read-only)."""

    paths: list[str]
    number_lines: bool = False
    number_non_blank: bool = False

    def build(self) -> list[str]:
        """Build cat command args."""
        resolved_paths = [validate_workspace_path(p) for p in self.paths]

        if not resolved_paths:
            raise UnsafeArgumentError("At least one path is required for cat")

        args = ["cat"]

        if self.number_lines:
            args.append("-n")
        if self.number_non_blank:
            args.append("-b")

        args.extend(str(p) for p in resolved_paths)
        return args


@dataclass
class DateArgs:
    """Safe date argument builder (timezone + format only, no date-setting)."""

    class DateFormat(str, Enum):
        """Predefined safe date formats."""

        ISO8601 = "%Y-%m-%d %H:%M:%S %Z"
        RFC2822 = "%a, %d %b %Y %H:%M:%S %z"
        UNIX_TS = "%s"
        DATE_ONLY = "%Y-%m-%d"
        TIME_ONLY = "%H:%M:%S"
        SHORT = "%d.%m.%Y %H:%M"  # German format

    timezone: str | None = None
    format: str | DateFormat | None = None

    def build(self) -> list[str]:
        """Build date command args."""
        args = ["date"]

        if self.timezone:
            # Validate timezone: must be in /etc/timezone database (simple check)
            if not re.match(r"^[A-Za-z_/\-]+$", self.timezone):
                raise UnsafeArgumentError(f"Invalid timezone format: {self.timezone}")
            # Example: TZ=Europe/Berlin
            # This would be set as environment variable, not arg
            # But we return it as a note for the caller

        fmt = self.format
        if fmt:
            if isinstance(fmt, self.DateFormat):
                fmt = fmt.value

            # Format string validation: only allow % directives and safe chars
            if not re.match(r"^[%a-zA-Z0-9\-:/ .]+$", fmt):
                raise UnsafeArgumentError(f"date format contains unsafe characters: {fmt}")

            args.extend(["+", fmt])

        return args


@dataclass
class MkdirArgs:
    """Safe mkdir argument builder for managed roots."""

    paths: list[str]
    create_parents: bool = True
    scope: PathScope = PathScope.WORKSPACE

    def build(self) -> list[str]:
        resolved_paths = [validate_managed_path(path, scope=self.scope) for path in self.paths]
        if not resolved_paths:
            raise UnsafeArgumentError("At least one path is required for mkdir")
        args = ["mkdir"]
        if self.create_parents:
            args.append("-p")
        args.extend(str(path) for path in resolved_paths)
        return args


@dataclass
class TouchArgs:
    """Safe touch argument builder for managed roots."""

    paths: list[str]
    scope: PathScope = PathScope.WORKSPACE

    def build(self) -> list[str]:
        resolved_paths = [validate_managed_path(path, scope=self.scope) for path in self.paths]
        if not resolved_paths:
            raise UnsafeArgumentError("At least one path is required for touch")
        return ["touch", *[str(path) for path in resolved_paths]]


@dataclass
class TeeArgs:
    """Safe tee argument builder for controlled writes via stdin."""

    path: str
    append: bool = False
    scope: PathScope = PathScope.WORKSPACE

    def build(self) -> list[str]:
        resolved_path = validate_managed_path(self.path, scope=self.scope)
        args = ["tee"]
        if self.append:
            args.append("-a")
        args.append(str(resolved_path))
        return args


# ── Registry for quick lookup ─────────────────────────────────────────────────

ARGS_BUILDERS: dict[str, type] = {
    "curl": CurlArgs,
    "find": FindArgs,
    "ls": LsArgs,
    "grep": GrepArgs,
    "head": HeadArgs,
    "tail": TailArgs,
    "cat": CatArgs,
    "date": DateArgs,
    "mkdir": MkdirArgs,
    "touch": TouchArgs,
    "tee": TeeArgs,
}


def get_args_builder(command: str) -> type | None:
    """Get the args builder class for a command.

    Args:
        command: Command name (e.g., "curl", "find", "ls").

    Returns:
        Args builder class, or None if not registered.
    """
    return ARGS_BUILDERS.get(command)


def build_safe_args(command: str, **kwargs) -> list[str]:
    """Build safe arguments for a command (convenience function).

    Args:
        command: Command name.
        **kwargs: Command-specific arguments.

    Returns:
        List of command + args ready for execution.

    Raises:
        ArgBuilderError: If arguments are unsafe or invalid.
    """
    builder_class = get_args_builder(command)
    if not builder_class:
        raise ArgBuilderError(f"No args builder registered for command: {command}")

    builder = builder_class(**kwargs)
    return builder.build()
