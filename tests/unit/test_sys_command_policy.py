"""Unit tests for generic sys command policy request format."""

from __future__ import annotations
import pytest

from services.tools.builtin import sys_command_policy
from services.tools.builtin.sys_command_policy import check_command_policy, check_command_request


@pytest.fixture(autouse=True)
def isolated_policy_db(tmp_path, monkeypatch):
    monkeypatch.setenv("LIARA_POLICY_DB_DIR", str(tmp_path / "db"))
    sys_command_policy._command_policy_sets.cache_clear()
    yield
    sys_command_policy._command_policy_sets.cache_clear()


def test_structured_non_profiled_command_allowed():
    r = check_command_request("python3", ["--version"])
    assert not r.allowed
    assert r.error_type == "unknown_flag"


def test_structured_curl_allowed():
    r = check_command_request("curl", ["-sI", "https://example.com"])
    assert r.allowed


def test_structured_curl_blocked_flag():
    r = check_command_request("curl", ["-k", "https://example.com"])
    assert not r.allowed
    assert r.error_type == "blocked_flag"


def test_structured_empty_command_blocked():
    r = check_command_request("", ["x"])
    assert not r.allowed
    assert r.error_type == "parse_error"


def test_structured_julia_version_allowed():
    r = check_command_request("julia", ["--version"])
    assert r.allowed
    assert r.policy_class == "language-bridge"


def test_structured_julia_workspace_script_allowed():
    r = check_command_request(
        "julia",
        ["--startup-file=no", "--quiet", "/home/liara/workspace/models/demo.jl"],
    )
    assert r.allowed


def test_structured_julia_eval_blocked():
    r = check_command_request("julia", ["-e", "println(1)"])
    assert not r.allowed
    assert r.error_type == "blocked_flag"


def test_structured_julia_external_script_blocked():
    r = check_command_request("julia", ["/etc/demo.jl"])
    assert not r.allowed
    assert r.error_type == "blocked_path"


def test_legacy_string_path_still_works():
    r = check_command_policy("curl -s https://example.com")
    assert r.allowed


@pytest.mark.parametrize("command_name", ["python3", "python"])
def test_python3_and_python_alias_are_allowed_for_safe_inline_execution(command_name: str):
    r = check_command_request(command_name, ["-c", "print(1)"])
    assert r.allowed
    assert r.policy_class == "standard"


def test_python_inline_text_mention_of_ctypes_is_not_blocked():
    r = check_command_request("python3", ["-c", 'print("ctypes")'])
    assert r.allowed
    assert r.policy_class == "language-bridge"


def test_python_inline_blocked_import_ctypes_is_blocked():
    r = check_command_request("python3", ["-c", "import ctypes\nprint('x')"])
    assert not r.allowed
    assert r.error_type == "blocked_code"
    assert "blocked import" in (r.error or "")
    assert r.policy_class == "language-bridge"


@pytest.mark.parametrize("command_name", ["python3", "python"])
@pytest.mark.parametrize(
    "args",
    [
        ["-m", "pytest", "-q", "tests"],
        ["-m", "pytest", "tests/test_worker.py"],
        ["-m", "pytest", "tests/test_worker.py::test_success"],
    ],
)
def test_python_pytest_allows_only_confined_test_execution(command_name: str, args: list[str]):
    result = check_command_request(command_name, args)
    assert result.allowed
    assert result.policy_class == "test-execution"


@pytest.mark.parametrize(
    "args",
    [
        ["-m", "pytest", "../tests"],
        ["-m", "pytest", "/etc"],
        ["-m", "pytest", "worker.py"],
        ["-m", "pytest", "-c", "tests/pytest.ini", "tests"],
        ["-m", "pytest", "-p", "evil_plugin", "tests"],
        ["-m", "pytest", "--rootdir=/", "tests"],
        ["-m", "pytest", "-q"],
    ],
)
def test_python_pytest_blocks_path_escape_and_runtime_extension(args: list[str]):
    result = check_command_request("python", args)
    assert not result.allowed
    assert result.error_type in {"blocked_flag", "blocked_path"}


def test_python_still_blocks_direct_script_execution():
    result = check_command_request("python", ["tests/test_worker.py"])
    assert not result.allowed
    assert result.error_type == "blocked_flag"


def test_workspace_venv_pip_allows_only_approved_noninteractive_dependencies():
    allowed = check_command_request(
        "venv-pip",
        ["install", "--disable-pip-version-check", "--no-input", "pydantic>=2.0", "pytest"],
    )
    assert allowed.allowed

    blocked_url = check_command_request(
        "venv-pip",
        ["install", "--disable-pip-version-check", "--no-input", "https://example.com/pkg.whl"],
    )
    assert not blocked_url.allowed
    assert blocked_url.error_type == "blocked_dependency"

    blocked_unknown = check_command_request(
        "venv-pip",
        ["install", "--disable-pip-version-check", "--no-input", "unknown-package"],
    )
    assert not blocked_unknown.allowed
    assert "allowlist" in (blocked_unknown.error or "")


@pytest.mark.parametrize("cmd", ["rm", "chmod", "chown", "mount", "su", "setpriv", "ip", "iptables", "nc", "ssh", "wget"])
def test_high_risk_commands_blocked(cmd: str):
    r = check_command_request(cmd, ["anything"])
    assert not r.allowed
    assert r.error_type == "blocked_command"


@pytest.mark.parametrize(
    ("command", "args", "expected_allowed"),
    [
        ("ls", ["/home/liara/workspace"], True),
        ("ls", ["/home/liara"], True),
        ("ls", ["/home/liara/.ssh"], False),
        ("ls", ["/etc"], False),
        ("grep", ["-n", "foo", "/home/liara/workspace/file.txt"], True),
        ("grep", ["foo"], False),
        ("head", ["-n", "10", "/home/liara/workspace/file.txt"], True),
        ("head", ["/etc/passwd"], False),
        ("tail", ["-n", "5", "/tmp/out.txt"], True),
        ("tail", ["-f", "/home/liara/workspace/file.txt"], False),
        ("date", ["+%Y-%m-%d"], True),
        ("date", ["+%H:%M:%S"], True),
        ("date", ["-u"], True),
        ("date", ["-s", "2025-01-01"], False),  # set time blocked
        ("time", ["+%Y-%m-%d %H:%M:%S %Z"], True),
        ("time", ["-s"], False),  # set time blocked
    ],
)
def test_new_file_commands_policy(command: str, args: list[str], expected_allowed: bool):
    r = check_command_request(command, args)
    assert r.allowed == expected_allowed


@pytest.mark.parametrize(
    ("command", "args", "expected_allowed"),
    [
        ("mkdir", ["-p", "/home/liara/workspace/out"], True),
        ("mkdir", ["-p", "/home/liara/temp/liara-out"], True),
        ("mkdir", ["/etc/test"], False),
        ("cp", ["/home/liara/workspace/a.txt", "/home/liara/workspace/b.txt"], True),
        ("cp", ["/home/liara/temp/a.txt", "/home/liara/workspace/b.txt"], True),
        ("cp", ["/home/liara/workspace/a.txt", "/home/liara/temp/b.txt"], True),
        ("cp", ["/home/liara/workspace/a.txt", "/home/liara/notes/b.txt"], True),
        ("cp", ["/home/liara/workspace/a.txt", "/tmp/b.txt"], False),
        ("cp", ["/home/liara/.ssh/id_rsa", "/home/liara/workspace/id_rsa"], False),
        ("mv", ["/home/liara/workspace/a.txt", "/home/liara/workspace/dir/a.txt"], True),
        ("mv", ["/home/liara/workspace/a.txt", "/etc/a.txt"], False),
        ("touch", ["/home/liara/workspace/new.txt"], True),
        ("touch", ["/home/liara/temp/new.txt"], True),
        ("cat", ["/tmp/file.txt"], False),
        ("cat", ["/home/liara/workspace/file.txt"], True),
        ("cat", ["/home/liara/notes/readme.txt"], True),
        ("cat", ["/home/liara/.ssh/id_rsa"], False),
        ("git", ["clone", "https://github.com/org/repo.git", "/home/liara/workspace/repo"], True),
        ("git", ["clone", "https://evil.example/repo.git", "/home/liara/workspace/repo"], False),
        ("git", ["status"], False),
        ("tar", ["-tf", "/home/liara/workspace/archive.tar"], True),
        ("tar", ["-xf", "/home/liara/workspace/archive.tar", "-C", "/home/liara/workspace/out"], True),
        ("tar", ["-xf", "/home/liara/workspace/archive.tar", "-C", "/etc"], False),
        ("tee", ["/home/liara/workspace/report.txt"], True),
        ("tee", ["-a", "/home/liara/workspace/report.txt"], True),
        ("tee", ["-a", "/home/liara/temp/report.txt"], True),
        ("tee", ["/home/liara/temp/report.txt"], True),
        ("tee", ["/etc/report.txt"], False),
        ("tee", ["/home/liara/workspace/a.txt", "/home/liara/workspace/b.txt"], False),
    ],
)
def test_controlled_write_and_special_policies(command: str, args: list[str], expected_allowed: bool):
    r = check_command_request(command, args)
    assert r.allowed == expected_allowed
