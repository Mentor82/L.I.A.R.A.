"""Tests for safe_args_builder module (Idea #51 implementation).

Tests cover:
  • Path validation (workspace-only, symlink resolution, escape prevention)
  • Per-command argument builders (curl, find, ls, grep, head, tail, cat, date)
  • Policy enforcement (no dangerous flags, no escapes, limits)
  • Error handling (unsafe args, malformed input)
"""

import pytest
from pathlib import Path

from services.tools.builtin.safe_args_builder import (
    ArgBuilderError,
    UnsafePathError,
    UnsafeArgumentError,
    validate_workspace_path,
    validate_managed_path,
    SAFE_WORKSPACE_ROOT,
    SAFE_TMP_ROOT,
    PathScope,
    CurlArgs,
    FindArgs,
    LsArgs,
    GrepArgs,
    HeadArgs,
    TailArgs,
    CatArgs,
    DateArgs,
    MkdirArgs,
    TouchArgs,
    TeeArgs,
    get_args_builder,
    build_safe_args,
)


class TestPathValidation:
    """Test workspace path validation."""

    def test_valid_absolute_path(self):
        """Relative paths are resolved within workspace."""
        result = validate_workspace_path(".")
        assert str(result).startswith(str(SAFE_WORKSPACE_ROOT.resolve()))

    def test_valid_relative_path(self):
        """Relative paths within workspace are accepted."""
        result = validate_workspace_path("config.json")
        assert str(result).startswith(str(SAFE_WORKSPACE_ROOT.resolve()))

    def test_valid_nested_path(self):
        """Nested relative paths are accepted."""
        result = validate_workspace_path("subdir/file.txt")
        assert "subdir/file.txt" in str(result) or "subdir\\file.txt" in str(result)

    def test_invalid_path_escape_dots(self):
        """Path traversal with .. is blocked."""
        with pytest.raises(UnsafePathError):
            validate_workspace_path("../../etc/passwd")

    def test_invalid_absolute_path_outside_workspace(self):
        """Absolute paths outside workspace are blocked."""
        with pytest.raises(UnsafePathError):
            validate_workspace_path("/etc/passwd")

    def test_invalid_absolute_path_root(self):
        """Root path is blocked."""
        with pytest.raises(UnsafePathError):
            validate_workspace_path("/")

    def test_empty_path_uses_workspace_root(self):
        """Empty path defaults to workspace root."""
        result = validate_workspace_path("")
        # Should resolve to workspace root or current dir within it
        assert str(result).startswith(str(SAFE_WORKSPACE_ROOT.resolve()))

    def test_validate_managed_temp_path(self):
        result = validate_managed_path("cache/report.txt", scope=PathScope.TEMP)
        assert str(result).startswith(str(SAFE_TMP_ROOT.resolve()))

    def test_validate_managed_any_allows_tmp_absolute(self):
        result = validate_managed_path("/home/liara/temp/liara/demo.txt", scope=PathScope.ANY_MANAGED)
        assert str(result).startswith(str(SAFE_TMP_ROOT.resolve()))


class TestCurlArgs:
    """Test curl argument builder."""

    def test_simple_http_url(self):
        """HTTP URLs are accepted."""
        builder = CurlArgs(url="http://example.com")
        args = builder.build()
        assert "curl" in args
        assert "http://example.com" in args
        assert "--max-time" in args

    def test_https_url(self):
        """HTTPS URLs are accepted."""
        builder = CurlArgs(url="https://api.example.com/v1/users")
        args = builder.build()
        assert "https://api.example.com/v1/users" in args

    def test_url_with_headers(self):
        """Headers are added safely."""
        builder = CurlArgs(
            url="https://api.example.com",
            headers={"Authorization": "Bearer token123", "Accept": "application/json"}
        )
        args = builder.build()
        assert "-H" in args
        assert "Authorization: Bearer token123" in args

    def test_invalid_url_no_scheme(self):
        """URLs without http/https are blocked."""
        builder = CurlArgs(url="example.com")
        with pytest.raises(UnsafeArgumentError):
            builder.build()

    def test_invalid_url_file_scheme(self):
        """file:// URLs are blocked."""
        builder = CurlArgs(url="file:///etc/passwd")
        with pytest.raises(UnsafeArgumentError):
            builder.build()

    def test_invalid_url_with_shell_metacharacters(self):
        """URLs with shell metacharacters are blocked."""
        builder = CurlArgs(url="https://example.com/`whoami`")
        with pytest.raises(UnsafeArgumentError):
            builder.build()

    def test_timeout_applied(self):
        """Timeout is added to args."""
        builder = CurlArgs(url="https://example.com", timeout=60)
        args = builder.build()
        assert "--max-time" in args
        assert "60" in args

    def test_max_size_applied(self):
        """Max size limit is applied."""
        builder = CurlArgs(url="https://example.com", max_size=1024*1024)
        args = builder.build()
        assert "--max-filesize" in args


class TestFindArgs:
    """Test find argument builder."""

    def test_find_in_current_dir(self):
        """Find in current workspace dir."""
        builder = FindArgs(path=".")
        args = builder.build()
        assert "find" in args
        # Path should be resolved workspace path

    def test_find_with_max_depth(self):
        """Max depth limits search depth."""
        builder = FindArgs(path=".", max_depth=2)
        args = builder.build()
        assert "-maxdepth" in args
        assert "2" in args

    def test_find_invalid_max_depth_too_high(self):
        """Max depth > 10 is rejected."""
        builder = FindArgs(path=".", max_depth=20)
        with pytest.raises(UnsafeArgumentError):
            builder.build()

    def test_find_invalid_max_depth_zero(self):
        """Max depth must be >= 1."""
        builder = FindArgs(path=".", max_depth=0)
        with pytest.raises(UnsafeArgumentError):
            builder.build()

    def test_find_by_type_file(self):
        """Find by file type."""
        builder = FindArgs(path=".", file_type="f")
        args = builder.build()
        assert "-type" in args
        assert "f" in args

    def test_find_by_type_directory(self):
        """Find by directory type."""
        builder = FindArgs(path=".", file_type="d")
        args = builder.build()
        assert "d" in args

    def test_find_invalid_file_type(self):
        """Invalid file type is rejected."""
        builder = FindArgs(path=".", file_type="x")
        with pytest.raises(UnsafeArgumentError):
            builder.build()

    def test_find_by_name_pattern(self):
        """Find by name pattern."""
        builder = FindArgs(path=".", name_pattern="*.txt")
        args = builder.build()
        assert "-name" in args
        assert "*.txt" in args

    def test_find_invalid_name_pattern_with_metacharacters(self):
        """Name patterns with dangerous chars are rejected."""
        builder = FindArgs(path=".", name_pattern="$(whoami)")
        with pytest.raises(UnsafeArgumentError):
            builder.build()


class TestLsArgs:
    """Test ls argument builder."""

    def test_simple_ls(self):
        """Simple ls call."""
        builder = LsArgs(path=".")
        args = builder.build()
        assert "ls" in args

    def test_ls_long_format(self):
        """Long format flag."""
        builder = LsArgs(path=".", long_format=True)
        args = builder.build()
        assert "-l" in args

    def test_ls_all_files(self):
        """Show all files (including hidden)."""
        builder = LsArgs(path=".", all_files=True)
        args = builder.build()
        assert "-a" in args

    def test_ls_recursive(self):
        """Recursive listing."""
        builder = LsArgs(path=".", recursive=True)
        args = builder.build()
        assert "-R" in args
        # Recursive should also use long format for safety
        assert "-l" in args

    def test_ls_invalid_path_outside_workspace(self):
        """Paths outside workspace are rejected."""
        builder = LsArgs(path="/root")
        with pytest.raises(UnsafePathError):
            builder.build()


class TestGrepArgs:
    """Test grep argument builder."""

    def test_simple_grep(self):
        """Simple grep pattern."""
        builder = GrepArgs(pattern="error", path=".")
        args = builder.build()
        assert "grep" in args
        assert "error" in args

    def test_grep_case_insensitive(self):
        """Case insensitive search."""
        builder = GrepArgs(pattern="error", path=".", case_insensitive=True)
        args = builder.build()
        assert "-i" in args

    def test_grep_line_numbers(self):
        """Include line numbers."""
        builder = GrepArgs(pattern="error", path=".", line_numbers=True)
        args = builder.build()
        assert "-n" in args

    def test_grep_count_only(self):
        """Count matches only."""
        builder = GrepArgs(pattern="error", path=".", count_only=True)
        args = builder.build()
        assert "-c" in args

    def test_grep_invert_match(self):
        """Invert match (lines NOT matching)."""
        builder = GrepArgs(pattern="error", path=".", invert_match=True)
        args = builder.build()
        assert "-v" in args

    def test_grep_invalid_pattern_with_shell_chars(self):
        """Patterns with shell metacharacters are rejected."""
        builder = GrepArgs(pattern="$(whoami)", path=".")
        with pytest.raises(UnsafeArgumentError):
            builder.build()

    def test_grep_invalid_pattern_with_backticks(self):
        """Backticks in pattern are rejected."""
        builder = GrepArgs(pattern="`rm -rf /`", path=".")
        with pytest.raises(UnsafeArgumentError):
            builder.build()


class TestHeadArgs:
    """Test head argument builder."""

    def test_simple_head(self):
        """Default head (10 lines)."""
        builder = HeadArgs(path="config.json")
        args = builder.build()
        assert "head" in args
        assert "-n" in args
        assert "10" in args

    def test_head_custom_lines(self):
        """Custom number of lines."""
        builder = HeadArgs(path="config.json", num_lines=50)
        args = builder.build()
        assert "50" in args

    def test_head_invalid_lines_too_many(self):
        """Line count > 1000 is rejected."""
        builder = HeadArgs(path="config.json", num_lines=2000)
        with pytest.raises(UnsafeArgumentError):
            builder.build()

    def test_head_invalid_lines_zero(self):
        """Line count must be >= 1."""
        builder = HeadArgs(path="config.json", num_lines=0)
        with pytest.raises(UnsafeArgumentError):
            builder.build()

    def test_head_invalid_path_outside_workspace(self):
        """Paths outside workspace are rejected."""
        builder = HeadArgs(path="/root/secret.txt")
        with pytest.raises(UnsafePathError):
            builder.build()


class TestTailArgs:
    """Test tail argument builder."""

    def test_simple_tail(self):
        """Default tail (10 lines)."""
        builder = TailArgs(path="app.log")
        args = builder.build()
        assert "tail" in args
        assert "10" in args

    def test_tail_with_follow(self):
        """Follow mode (-f flag)."""
        builder = TailArgs(path="app.log", follow=True)
        args = builder.build()
        assert "-f" in args

    def test_tail_custom_lines(self):
        """Custom line count."""
        builder = TailArgs(path="app.log", num_lines=100)
        args = builder.build()
        assert "100" in args

    def test_tail_invalid_lines(self):
        """Line count validation."""
        builder = TailArgs(path="app.log", num_lines=5000)
        with pytest.raises(UnsafeArgumentError):
            builder.build()


class TestCatArgs:
    """Test cat argument builder."""

    def test_single_file(self):
        """Cat single file."""
        builder = CatArgs(paths=["config.json"])
        args = builder.build()
        assert "cat" in args
        # Path will be resolved, so just check it's there

    def test_multiple_files(self):
        """Cat multiple files."""
        builder = CatArgs(paths=["file1.txt", "file2.txt"])
        args = builder.build()
        assert "cat" in args
        # Both files should be in args

    def test_cat_number_lines(self):
        """Number lines flag."""
        builder = CatArgs(paths=["file.txt"], number_lines=True)
        args = builder.build()
        assert "-n" in args

    def test_cat_number_non_blank(self):
        """Number non-blank lines flag."""
        builder = CatArgs(paths=["file.txt"], number_non_blank=True)
        args = builder.build()
        assert "-b" in args

    def test_cat_no_files_error(self):
        """At least one file is required."""
        builder = CatArgs(paths=[])
        with pytest.raises(UnsafeArgumentError):
            builder.build()

    def test_cat_invalid_path(self):
        """Paths outside workspace are rejected."""
        builder = CatArgs(paths=["/etc/passwd"])
        with pytest.raises(UnsafePathError):
            builder.build()


class TestDateArgs:
    """Test date argument builder."""

    def test_simple_date(self):
        """Simple date command."""
        builder = DateArgs()
        args = builder.build()
        assert "date" in args

    def test_date_iso8601_format(self):
        """ISO8601 format."""
        builder = DateArgs(format=DateArgs.DateFormat.ISO8601)
        args = builder.build()
        assert "+" in args
        assert "%Y-%m-%d %H:%M:%S %Z" in args

    def test_date_unix_timestamp(self):
        """Unix timestamp format."""
        builder = DateArgs(format=DateArgs.DateFormat.UNIX_TS)
        args = builder.build()
        assert "%s" in args

    def test_date_short_german_format(self):
        """German short date format."""
        builder = DateArgs(format=DateArgs.DateFormat.SHORT)
        args = builder.build()
        assert "%d.%m.%Y %H:%M" in args

    def test_date_custom_valid_format(self):
        """Custom format with only safe characters."""
        builder = DateArgs(format="%Y-%m-%d")
        args = builder.build()
        assert "%Y-%m-%d" in args

    def test_date_invalid_format_with_shell_chars(self):
        """Format strings with shell chars are rejected."""
        builder = DateArgs(format="$(whoami)")
        with pytest.raises(UnsafeArgumentError):
            builder.build()

    def test_date_timezone_valid(self):
        """Valid timezone format."""
        builder = DateArgs(timezone="Europe/Berlin")
        args = builder.build()
        assert "date" in args

    def test_date_invalid_timezone_format(self):
        """Invalid timezone format is rejected."""
        builder = DateArgs(timezone="$(whoami)")
        with pytest.raises(UnsafeArgumentError):
            builder.build()


class TestRegistry:
    """Test args builder registry and convenience functions."""

    def test_get_builder_curl(self):
        """Get curl builder from registry."""
        builder = get_args_builder("curl")
        assert builder is CurlArgs

    def test_get_builder_find(self):
        """Get find builder from registry."""
        builder = get_args_builder("find")
        assert builder is FindArgs

    def test_get_builder_tee(self):
        builder = get_args_builder("tee")
        assert builder is TeeArgs

    def test_get_builder_unknown_command(self):
        """Unknown command returns None."""
        builder = get_args_builder("unknown")
        assert builder is None

    def test_build_safe_args_curl(self):
        """Convenience function for curl."""
        args = build_safe_args("curl", url="https://example.com")
        assert "curl" in args
        assert "https://example.com" in args

    def test_build_safe_args_find(self):
        """Convenience function for find."""
        args = build_safe_args("find", path=".", max_depth=1)
        assert "find" in args
        assert "-maxdepth" in args

    def test_build_safe_args_unknown_command(self):
        """Unknown command raises error."""
        with pytest.raises(ArgBuilderError):
            build_safe_args("unknown", foo="bar")

    def test_build_safe_args_invalid_kwargs(self):
        """Invalid kwargs for builder raise error."""
        with pytest.raises(TypeError):
            build_safe_args("curl", invalid_arg=True)


class TestIntegration:
    """Integration tests combining multiple builders."""

    def test_workflow_find_then_cat(self):
        """Realistic workflow: find files then cat them."""
        # Find python files
        find_args = build_safe_args("find", path=".", name_pattern="*.py", file_type="f")
        assert "find" in find_args
        assert "*.py" in find_args

        # Cat a specific file
        cat_args = build_safe_args("cat", paths=["script.py"])
        assert "cat" in cat_args

    def test_workflow_ls_then_grep(self):
        """Realistic workflow: list directory then grep for pattern."""
        # List files
        ls_args = build_safe_args("ls", path=".", long_format=True)
        assert "ls" in ls_args
        assert "-l" in ls_args

        # Grep for error messages
        grep_args = build_safe_args("grep", pattern="error", path=".")
        assert "grep" in grep_args
        assert "error" in grep_args

    def test_workflow_curl_with_headers(self):
        """Realistic workflow: fetch with auth header."""
        curl_args = build_safe_args(
            "curl",
            url="https://api.example.com/data",
            headers={"Authorization": "Bearer token", "Accept": "application/json"}
        )
        assert "curl" in curl_args
        assert "https://api.example.com/data" in curl_args
        assert "-H" in curl_args


class TestWriteBuilders:
    def test_mkdir_workspace(self):
        args = MkdirArgs(paths=["reports"], scope=PathScope.WORKSPACE).build()
        assert args[0] == "mkdir"
        assert str(SAFE_WORKSPACE_ROOT.resolve()) in " ".join(args)

    def test_touch_temp(self):
        args = TouchArgs(paths=["scratch.txt"], scope=PathScope.TEMP).build()
        assert args[0] == "touch"
        assert str(SAFE_TMP_ROOT.resolve()) in " ".join(args)

    def test_tee_append_temp(self):
        args = TeeArgs(path="report.txt", append=True, scope=PathScope.TEMP).build()
        assert args[:2] == ["tee", "-a"]
        assert str(SAFE_TMP_ROOT.resolve()) in " ".join(args)

    def test_build_safe_args_touch(self):
        args = build_safe_args("touch", paths=["new.txt"], scope=PathScope.WORKSPACE)
        assert args[0] == "touch"
