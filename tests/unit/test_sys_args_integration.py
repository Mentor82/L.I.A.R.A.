"""Integration tests for sys_args_integration module.

Tests the bridge between sys_selector and safe_args_builder.
"""

import pytest
from services.orchestrator.sys_args_integration import (
    SafeCommandBuilder,
    SysArgsIntegrationError,
    build_safe_curl_command,
    build_safe_cat_command,
    build_safe_find_command,
    build_safe_grep_command,
    build_safe_ls_command,
    build_safe_head_command,
    build_safe_tail_command,
    build_safe_date_command,
    build_safe_mkdir_command,
    build_safe_touch_command,
    build_safe_tee_command,
)
from services.orchestrator.sys_selector import CommandCategory


class TestSafeCommandBuilder:
    """Test the SafeCommandBuilder wrapper."""

    def test_build_curl(self):
        """Build a curl command selection."""
        builder = SafeCommandBuilder(
            command="curl",
            category=CommandCategory.FETCH,
            context="agent_url_fetch",
            intent="url_fetch",
            builder_kwargs={"url": "https://example.com"},
        )
        selection = builder.build()
        assert selection.command == "curl"
        assert selection.context == "agent_url_fetch"
        assert selection.category == CommandCategory.FETCH
        assert "curl" in selection.args
        assert "https://example.com" in selection.args

    def test_build_find(self):
        """Build a find command selection."""
        builder = SafeCommandBuilder(
            command="find",
            category=CommandCategory.READ_INSPECT,
            context="agent_workspace_list",
            intent="workspace",
            builder_kwargs={"path": ".", "max_depth": 2},
        )
        selection = builder.build()
        assert selection.command == "find"
        assert selection.category == CommandCategory.READ_INSPECT
        assert "find" in selection.args

    def test_build_with_extra_metadata(self):
        """Extra metadata is preserved in selection."""
        builder = SafeCommandBuilder(
            command="curl",
            category=CommandCategory.FETCH,
            context="agent_url_fetch",
            intent="url_fetch",
            builder_kwargs={"url": "https://api.example.com"},
            extra={"source": "user_query", "request_id": "req123"},
        )
        selection = builder.build()
        assert selection.extra["source"] == "user_query"
        assert selection.extra["request_id"] == "req123"

    def test_build_with_invalid_args(self):
        """Invalid builder kwargs raise integration error."""
        builder = SafeCommandBuilder(
            command="curl",
            category=CommandCategory.FETCH,
            context="agent_url_fetch",
            intent="url_fetch",
            builder_kwargs={"url": "not-a-url"},  # Invalid URL
        )
        with pytest.raises(SysArgsIntegrationError):
            builder.build()


class TestSafeCurlCommand:
    """Test curl command builder."""

    def test_simple_url(self):
        """Build curl for simple HTTPS URL."""
        selection = build_safe_curl_command("https://api.example.com/data")
        assert selection.command == "curl"
        assert "https://api.example.com/data" in selection.args
        assert selection.category == CommandCategory.FETCH

    def test_with_headers(self):
        """Build curl with custom headers."""
        selection = build_safe_curl_command(
            "https://api.example.com",
            headers={"Authorization": "Bearer token", "Accept": "application/json"}
        )
        assert "-H" in selection.args
        assert "Authorization: Bearer token" in selection.args

    def test_invalid_url(self):
        """Invalid URL raises error."""
        with pytest.raises(SysArgsIntegrationError):
            build_safe_curl_command("ftp://example.com")


class TestSafeCatCommand:
    """Test cat command builder."""

    def test_single_file(self):
        """Cat a single file."""
        selection = build_safe_cat_command(["config.json"])
        assert selection.command == "cat"
        assert "cat" in selection.args

    def test_multiple_files(self):
        """Cat multiple files."""
        selection = build_safe_cat_command(["file1.txt", "file2.txt"])
        assert selection.command == "cat"
        assert "cat" in selection.args

    def test_invalid_path_outside_workspace(self):
        """Paths outside workspace raise error."""
        with pytest.raises(SysArgsIntegrationError):
            build_safe_cat_command(["/etc/passwd"])


class TestSafeFindCommand:
    """Test find command builder."""

    def test_find_all_files(self):
        """Find all files in workspace."""
        selection = build_safe_find_command()
        assert selection.command == "find"
        assert "find" in selection.args

    def test_find_with_limits(self):
        """Find with depth and type limits."""
        selection = build_safe_find_command(
            path=".",
            max_depth=2,
            file_type="f",
            name_pattern="*.py"
        )
        assert selection.command == "find"
        assert "-maxdepth" in selection.args

    def test_find_invalid_depth(self):
        """Invalid max_depth raises error."""
        with pytest.raises(SysArgsIntegrationError):
            build_safe_find_command(max_depth=20)


class TestSafeGrepCommand:
    """Test grep command builder."""

    def test_simple_grep(self):
        """Grep for pattern."""
        selection = build_safe_grep_command("error")
        assert selection.command == "grep"
        assert "error" in selection.args

    def test_grep_with_options(self):
        """Grep with multiple options."""
        selection = build_safe_grep_command(
            "error",
            case_insensitive=True,
            line_numbers=True
        )
        assert selection.command == "grep"
        assert "-i" in selection.args
        assert "-n" in selection.args

    def test_grep_invalid_pattern(self):
        """Invalid pattern raises error."""
        with pytest.raises(SysArgsIntegrationError):
            build_safe_grep_command("$(whoami)")


class TestSafeLsCommand:
    """Test ls command builder."""

    def test_simple_ls(self):
        """Simple directory listing."""
        selection = build_safe_ls_command()
        assert selection.command == "ls"
        assert "ls" in selection.args

    def test_ls_with_options(self):
        """Ls with long format and all files."""
        selection = build_safe_ls_command(
            long_format=True,
            all_files=True
        )
        assert selection.command == "ls"

    def test_ls_invalid_path(self):
        """Invalid path raises error."""
        with pytest.raises(SysArgsIntegrationError):
            build_safe_ls_command(path="/root")


class TestSafeHeadCommand:
    """Test head command builder."""

    def test_head_default(self):
        """Head with default lines (10)."""
        selection = build_safe_head_command("file.txt")
        assert selection.command == "head"
        assert "10" in selection.args

    def test_head_custom_lines(self):
        """Head with custom line count."""
        selection = build_safe_head_command("file.txt", num_lines=50)
        assert "50" in selection.args

    def test_head_invalid_lines(self):
        """Invalid line count raises error."""
        with pytest.raises(SysArgsIntegrationError):
            build_safe_head_command("file.txt", num_lines=5000)


class TestSafeTailCommand:
    """Test tail command builder."""

    def test_tail_default(self):
        """Tail with default lines (10)."""
        selection = build_safe_tail_command("app.log")
        assert selection.command == "tail"
        assert "10" in selection.args

    def test_tail_with_follow(self):
        """Tail with follow mode."""
        selection = build_safe_tail_command("app.log", follow=True)
        assert "-f" in selection.args

    def test_tail_invalid_lines(self):
        """Invalid line count raises error."""
        with pytest.raises(SysArgsIntegrationError):
            build_safe_tail_command("app.log", num_lines=5000)


class TestSafeDateCommand:
    """Test date command builder."""

    def test_date_default(self):
        """Date with default format."""
        selection = build_safe_date_command()
        assert selection.command == "date"
        assert "date" in selection.args

    def test_date_with_format(self):
        """Date with custom format."""
        selection = build_safe_date_command(format="%Y-%m-%d")
        assert "date" in selection.args

    def test_date_with_timezone(self):
        """Date with timezone."""
        selection = build_safe_date_command(timezone="Europe/Berlin")
        assert "date" in selection.args


class TestSafeWriteCommands:
    def test_build_mkdir_workspace(self):
        selection = build_safe_mkdir_command(["reports"])
        assert selection.command == "mkdir"
        assert selection.category == CommandCategory.WRITE_MUTATE

    def test_build_touch_temp(self):
        selection = build_safe_touch_command(["cache.txt"], temp=True)
        assert selection.command == "touch"
        assert selection.extra["storage_scope"] == "temp"

    def test_build_tee_with_stdin(self):
        selection = build_safe_tee_command("report.txt", stdin_text="hello")
        assert selection.command == "tee"
        assert selection.stdin_text == "hello"
        assert selection.extra["write_mode"] == "overwrite"

    def test_build_tee_append_temp(self):
        selection = build_safe_tee_command("report.txt", stdin_text="hello", append=True, temp=True)
        assert "-a" in selection.args
        assert selection.extra["storage_scope"] == "temp"


class TestIntegration:
    """Integration tests combining multiple commands."""

    def test_workflow_curl_then_grep(self):
        """Realistic: fetch URL then search output."""
        curl_cmd = build_safe_curl_command("https://api.example.com/logs")
        assert curl_cmd.command == "curl"

        grep_cmd = build_safe_grep_command("error")
        assert grep_cmd.command == "grep"

    def test_workflow_find_then_cat(self):
        """Realistic: find Python files then read first one."""
        find_cmd = build_safe_find_command(name_pattern="*.py", file_type="f")
        assert find_cmd.command == "find"

        cat_cmd = build_safe_cat_command(["script.py"])
        assert cat_cmd.command == "cat"

    def test_all_builders_return_selections(self):
        """All builders return valid SysCommandSelection objects."""
        selections = [
            build_safe_curl_command("https://example.com"),
            build_safe_cat_command(["test.txt"]),
            build_safe_find_command(),
            build_safe_grep_command("pattern"),
            build_safe_ls_command(),
            build_safe_head_command("file.txt"),
            build_safe_tail_command("file.txt"),
            build_safe_date_command(),
        ]

        for selection in selections:
            assert selection.command is not None
            assert selection.args is not None
            assert isinstance(selection.args, list)
            assert len(selection.args) > 0
