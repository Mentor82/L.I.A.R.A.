"""Integration between sys_selector and safe_args_builder.

This module demonstrates how to use the safe args builder to construct
command arguments for sys_selector commands.

Functions:
  • build_safe_select_sys_command() - alternative to select_sys_command() using safe builders
  • migrate_existing_commands()     - helper to identify existing commands that should use builders
"""

from __future__ import annotations
from dataclasses import dataclass, field

from services.orchestrator.sys_selector import (
    SysCommandSelection,
    CommandCategory,
)
from services.tools.builtin.safe_args_builder import (
    build_safe_args,
    ArgBuilderError,
    PathScope,
)


class SysArgsIntegrationError(Exception):
    """Error during sys/args integration."""

    pass


@dataclass
class SafeCommandBuilder:
    """Helper to safely build sys command selections using safe args builders."""

    command: str
    category: CommandCategory
    context: str
    intent: str
    builder_kwargs: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    def build(self) -> SysCommandSelection:
        """Build a SysCommandSelection using the safe args builder.

        Returns:
            SysCommandSelection with safely-constructed args.

        Raises:
            SysArgsIntegrationError: If args building fails.
        """
        try:
            args = build_safe_args(self.command, **self.builder_kwargs)
            return SysCommandSelection(
                command=self.command,
                args=args,
                context=self.context,
                intent=self.intent,
                category=self.category,
                extra=self.extra,
            )
        except ArgBuilderError as e:
            raise SysArgsIntegrationError(
                f"Failed to build safe args for {self.command}: {e}"
            ) from e


# ── Example safe command builders ─────────────────────────────────────────────

def build_safe_curl_command(url: str, headers: dict[str, str] | None = None) -> SysCommandSelection:
    """Build a safe curl command selection."""
    builder = SafeCommandBuilder(
        command="curl",
        category=CommandCategory.FETCH,
        context="agent_url_fetch",
        intent="url_fetch",
        builder_kwargs={"url": url, "headers": headers or {}},
        extra={"url": url},
    )
    return builder.build()


def build_safe_cat_command(paths: list[str]) -> SysCommandSelection:
    """Build a safe cat command selection for reading files."""
    builder = SafeCommandBuilder(
        command="cat",
        category=CommandCategory.READ_INSPECT,
        context="agent_workspace_read",
        intent="workspace",
        builder_kwargs={"paths": paths},
    )
    return builder.build()


def build_safe_find_command(
    path: str = ".",
    max_depth: int | None = None,
    name_pattern: str | None = None,
    file_type: str | None = None,
) -> SysCommandSelection:
    """Build a safe find command selection."""
    builder = SafeCommandBuilder(
        command="find",
        category=CommandCategory.READ_INSPECT,
        context="agent_workspace_list",
        intent="workspace",
        builder_kwargs={
            "path": path,
            "max_depth": max_depth,
            "name_pattern": name_pattern,
            "file_type": file_type,
        },
    )
    return builder.build()


def build_safe_grep_command(
    pattern: str,
    path: str = ".",
    case_insensitive: bool = False,
    line_numbers: bool = False,
) -> SysCommandSelection:
    """Build a safe grep command selection."""
    builder = SafeCommandBuilder(
        command="grep",
        category=CommandCategory.READ_INSPECT,
        context="agent_workspace_search",
        intent="workspace",
        builder_kwargs={
            "pattern": pattern,
            "path": path,
            "case_insensitive": case_insensitive,
            "line_numbers": line_numbers,
        },
    )
    return builder.build()


def build_safe_ls_command(
    path: str = ".",
    long_format: bool = False,
    all_files: bool = False,
) -> SysCommandSelection:
    """Build a safe ls command selection."""
    builder = SafeCommandBuilder(
        command="ls",
        category=CommandCategory.READ_INSPECT,
        context="agent_workspace_list",
        intent="workspace",
        builder_kwargs={
            "path": path,
            "long_format": long_format,
            "all_files": all_files,
        },
    )
    return builder.build()


def build_safe_head_command(path: str, num_lines: int = 10) -> SysCommandSelection:
    """Build a safe head command selection."""
    builder = SafeCommandBuilder(
        command="head",
        category=CommandCategory.READ_INSPECT,
        context="agent_workspace_read",
        intent="workspace",
        builder_kwargs={"path": path, "num_lines": num_lines},
    )
    return builder.build()


def build_safe_tail_command(
    path: str, num_lines: int = 10, follow: bool = False
) -> SysCommandSelection:
    """Build a safe tail command selection."""
    builder = SafeCommandBuilder(
        command="tail",
        category=CommandCategory.READ_INSPECT,
        context="agent_workspace_read",
        intent="workspace",
        builder_kwargs={"path": path, "num_lines": num_lines, "follow": follow},
    )
    return builder.build()


def build_safe_date_command(
    timezone: str | None = None,
    format: str | None = None,
) -> SysCommandSelection:
    """Build a safe date command selection."""
    builder = SafeCommandBuilder(
        command="date",
        category=CommandCategory.READ_INSPECT,
        context="agent_datetime_fetch",
        intent="datetime",
        builder_kwargs={"timezone": timezone, "format": format},
    )
    return builder.build()


def build_safe_mkdir_command(paths: list[str], *, temp: bool = False) -> SysCommandSelection:
    builder = SafeCommandBuilder(
        command="mkdir",
        category=CommandCategory.WRITE_MUTATE,
        context="agent_workspace_mkdir",
        intent="workspace_write",
        builder_kwargs={"paths": paths, "scope": PathScope.TEMP if temp else PathScope.WORKSPACE},
        extra={"storage_scope": "temp" if temp else "workspace", "target_path": paths[0], "write_mode": "mkdir"},
    )
    return builder.build()


def build_safe_touch_command(paths: list[str], *, temp: bool = False) -> SysCommandSelection:
    builder = SafeCommandBuilder(
        command="touch",
        category=CommandCategory.WRITE_MUTATE,
        context="agent_workspace_touch",
        intent="workspace_write",
        builder_kwargs={"paths": paths, "scope": PathScope.TEMP if temp else PathScope.WORKSPACE},
        extra={"storage_scope": "temp" if temp else "workspace", "target_path": paths[0], "write_mode": "touch"},
    )
    return builder.build()


def build_safe_tee_command(path: str, *, stdin_text: str, append: bool = False, temp: bool = False) -> SysCommandSelection:
    builder = SafeCommandBuilder(
        command="tee",
        category=CommandCategory.WRITE_MUTATE,
        context="agent_workspace_write",
        intent="workspace_write",
        builder_kwargs={"path": path, "append": append, "scope": PathScope.TEMP if temp else PathScope.WORKSPACE},
        extra={"storage_scope": "temp" if temp else "workspace", "target_path": path, "write_mode": "append" if append else "overwrite"},
    )
    selection = builder.build()
    return SysCommandSelection(
        command=selection.command,
        args=selection.args,
        context=selection.context,
        intent=selection.intent,
        category=selection.category,
        stdin_text=stdin_text,
        extra=selection.extra,
    )


# ── Migration guide ───────────────────────────────────────────────────────────

"""
## Migration Strategy: From Manual Args to Safe Args Builders

### Current State (manual args in sys_selector)
```python
SysCommandSelection(
    command="curl",
    args=["-s", "-L", "-m", "15", "-A", "Mozilla/...", url],
    context="agent_url_fetch",
    ...
)
```

### Target State (using safe args builder)
```python
build_safe_curl_command(url, headers={"User-Agent": "Mozilla/..."})
```

### Benefits
1. **Validation**: All arguments validated at build time
2. **Policy Compliance**: Automatically enforces safe defaults
3. **Maintainability**: Clear what arguments are allowed for each command
4. **Testability**: Args builders have comprehensive test coverage
5. **Extension**: Easy to add new commands or restrict existing ones

### Migration Checklist
- [ ] Import safe command builders in sys_selector.py
- [ ] Replace manual curl args → build_safe_curl_command()
- [ ] Replace manual cat args → build_safe_cat_command()
- [ ] Replace manual find args → build_safe_find_command()
- [ ] Add new handlers for date, grep, ls, head, tail
- [ ] Run existing sys_selector tests to ensure compatibility
- [ ] Update sys_selector documentation with examples
- [ ] Add integration tests for each safe command builder
"""
