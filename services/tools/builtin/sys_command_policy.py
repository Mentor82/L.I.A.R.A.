"""Generic command policy engine for sys tool commands.

This module is command-agnostic and can host multiple command profiles.
Each profile may use local policy DB files in `db/<command>/(w,g,b).db`.
"""

from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass
from functools import lru_cache
import os
from urllib.parse import urlparse

from .policy_db import load_command_policy


@dataclass
class PolicyResult:
    allowed: bool
    error: str | None = None
    error_type: str | None = None
    policy_class: str = "standard"


# ── python3 policy ────────────────────────────────────────────────────────────
# Whitelist: flags that are always safe for the one-liner use-case
# Greylist:  flags that need contextual review (module imports, file interaction)
# Blacklist: flags/constructs that allow file writes, network access, or arbitrary import
_PYTHON3_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-c",          # inline code — validated separately
        "flag:-u",          # unbuffered output
        "flag:-q",          # quiet mode
        "flag:-B",          # don't write .pyc files
        "flag:-OO",         # strip docstrings
    ),
    "g": (
        "flag:-m",          # module run — allowed only for safe modules
        "flag:-W",          # warning control
    ),
    "b": (
        "flag:-i",          # interactive mode
        "flag:--",          # end-of-flags separator
        "import:os",        # os module (shell access, file system)
        "import:sys",       # sys module (can call sys.exit, argv manipulation)
        "import:subprocess",
        "import:shutil",
        "import:socket",
        "import:ftplib",
        "import:smtplib",
        "import:http",
        "import:urllib",
        "import:requests",
        "import:ctypes",
        "import:importlib",
        "call:open(",        # file open
        "call:exec(",        # dynamic execution
        "call:eval(",        # dynamic evaluation
        "call:compile(",     # code compilation
        "call:__import__",   # dynamic imports
        "call:breakpoint",   # debugger
    ),
}

_DEPENDENCY_SPEC_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?"
    r"(?:(?:==|>=|<=|~=|!=|>|<)[A-Za-z0-9.*+!_-]+(?:,(?:==|>=|<=|~=|!=|>|<)[A-Za-z0-9.*+!_-]+)*)?$"
)

_PYTEST_SAFE_FLAGS = frozenset({"-q", "--disable-warnings", "--maxfail=1"})
_PYTEST_SELECTOR_RE = re.compile(
    r"^(?:\./)?tests(?:/[A-Za-z0-9_.-]+)*"
    r"(?:::[A-Za-z_][A-Za-z0-9_]*(?:\[[A-Za-z0-9_.:,\-]+\])?)*$"
)


def _dependency_name(spec: str) -> str:
    match = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", spec)
    return re.sub(r"[-_.]+", "-", match.group(1)).lower() if match else ""


def _dependency_allowlist() -> frozenset[str]:
    raw = os.getenv("LIARA_AGENT_DEPENDENCY_ALLOWLIST", "pydantic,pytest")
    return frozenset(
        re.sub(r"[-_.]+", "-", item.strip()).lower()
        for item in raw.split(",")
        if item.strip()
    )

# ── julia policy ─────────────────────────────────────────────────────────────
# Julia is permitted only for deterministic script execution from safe roots.
# Free-form eval/load/project flags stay blocked at the /sys layer.
_JULIA_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:--startup-file=no",
        "flag:--quiet",
        "flag:-q",
        "flag:--version",
        "flag:-v",
        "path_prefix:/home/liara/workspace",
        "path_prefix:/home/liara/temp",
    ),
    "g": (),
    "b": (
        "flag:-e",
        "flag:--eval",
        "flag:-E",
        "flag:--print",
        "flag:-L",
        "flag:--load",
        "flag:-i",
        "flag:--interactive",
        "flag:--project",
        "path_prefix:/etc",
        "path_prefix:/root",
        "path_prefix:/proc",
        "path_prefix:/sys",
        "path_prefix:/dev",
        "path_prefix:/mnt",
        "path_prefix:/media",
        "path_prefix:/home/liara/.ssh",
        "path_prefix:/home/liara/.gnupg",
        "path_prefix:/home/liara/.config",
    ),
}

# ── find policy ───────────────────────────────────────────────────────────────
# find is allowed only in /home/liara/workspace (enforced by path check)
_FIND_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-maxdepth",
        "flag:-mindepth",
        "flag:-type",
        "flag:-name",
        "flag:-iname",
        "flag:-size",
        "flag:-newer",
        "flag:-mtime",
        "flag:-atime",
        "type_arg:f",   # regular files
        "type_arg:d",   # directories
        "type_arg:l",   # symlinks
    ),
    "g": (
        "flag:-print",
        "flag:-print0",
    ),
    "b": (
        "flag:-exec",       # arbitrary command execution
        "flag:-execdir",
        "flag:-ok",
        "flag:-delete",     # destructive
        "flag:-ls",
        "path:/etc",        # blocked path prefixes
        "path:/root",
        "path:/proc",
        "path:/sys",
        "path:/dev",
        "path:/mnt",        # Windows mounts
        "path:/media",
    ),
}

# ── cat policy ────────────────────────────────────────────────────────────────
# cat is read-only but must be restricted to safe paths
_CAT_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-n",      # number lines
        "flag:-b",      # number non-blank lines
        "flag:-s",      # squeeze blanks
        "flag:-A",      # show all (debug)
        "flag:-v",      # show non-printing
        "path_prefix:/home/liara",
    ),
    "g": (),
    "b": (
        "path_prefix:/etc",
        "path_prefix:/root",
        "path_prefix:/proc",
        "path_prefix:/sys",
        "path_prefix:/dev",
        "path_prefix:/mnt",
        "path_prefix:/media",
        "path_prefix:/home/liara/.ssh",
        "path_prefix:/home/liara/.gnupg",
        "path_prefix:/home/liara/.config",
        "flag:>",       # redirect (shell, normally caught by wsl_executor but belt+braces)
        "flag:>>",
    ),
}

# ── ls policy ────────────────────────────────────────────────────────────────
# ls is read-only but should stay inside safe roots
_LS_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-1",
        "flag:-a",
        "flag:-A",
        "flag:-l",
        "flag:-h",
        "flag:-R",
        "flag:-t",
        "flag:-S",
        "path_prefix:/home/liara",
    ),
    "g": (
        "flag:--color",
        "path_prefix:/tmp",
    ),
    "b": (
        "path_prefix:/etc",
        "path_prefix:/root",
        "path_prefix:/proc",
        "path_prefix:/sys",
        "path_prefix:/dev",
        "path_prefix:/mnt",
        "path_prefix:/media",
        "path_prefix:/home/liara/.ssh",
        "path_prefix:/home/liara/.gnupg",
        "path_prefix:/home/liara/.config",
    ),
}

# ── grep policy ──────────────────────────────────────────────────────────────
_GREP_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-n",
        "flag:-i",
        "flag:-w",
        "flag:-E",
        "flag:-F",
        "flag:-m",
        "path_prefix:/home/liara/workspace",
    ),
    "g": (
        "path_prefix:/tmp",
    ),
    "b": (
        "flag:-r",
        "flag:-R",
        "flag:--include",
        "flag:--exclude",
        "flag:--exclude-dir",
        "flag:-f",
        "path_prefix:/etc",
        "path_prefix:/root",
        "path_prefix:/proc",
        "path_prefix:/sys",
        "path_prefix:/dev",
        "path_prefix:/mnt",
        "path_prefix:/media",
        "path_prefix:/home/liara/.ssh",
        "path_prefix:/home/liara/.gnupg",
        "path_prefix:/home/liara/.config",
    ),
}

# ── jq policy ───────────────────────────────────────────────────────────────
_JQ_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-r", "flag:--raw-output",
        "flag:-c", "flag:--compact-output",
        "flag:-n", "flag:--null-input",
        "flag:-e", "flag:--exit-status",
        "flag:-s", "flag:--slurp",
        "flag:-R", "flag:--raw-input",
        "flag:-j", "flag:--join-output",
        "flag:-a", "flag:--ascii-output",
        "flag:-C", "flag:--color-output",
        "flag:-M", "flag:--monochrome-output",
        "path_prefix:/home/liara/workspace",
    ),
    "g": (
        "flag:-f", "flag:--from-file",
        "flag:--arg",
        "flag:--argjson",
        "flag:--args",
        "flag:--jsonargs",
    ),
    "b": (
        "path_prefix:/etc",
        "path_prefix:/root",
        "path_prefix:/proc",
        "path_prefix:/sys",
        "path_prefix:/dev",
        "path_prefix:/mnt",
        "path_prefix:/media",
        "path_prefix:/home/liara/.ssh",
        "path_prefix:/home/liara/.gnupg",
        "path_prefix:/home/liara/.config",
    ),
}

# ── tee policy ──────────────────────────────────────────────────────────────
_TEE_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "path_prefix:/home/liara/workspace",
        "path_prefix:/home/liara/temp",
    ),
    "g": (
        "flag:-a",
        "flag:--append",
    ),
    "b": (
        "path_prefix:/etc",
        "path_prefix:/root",
        "path_prefix:/proc",
        "path_prefix:/sys",
        "path_prefix:/dev",
        "path_prefix:/mnt",
        "path_prefix:/media",
        "path_prefix:/home/liara/.ssh",
        "path_prefix:/home/liara/.gnupg",
        "path_prefix:/home/liara/.config",
    ),
}

# ── head / tail policy ───────────────────────────────────────────────────────
_HEAD_TAIL_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-n",
        "flag:-c",
        "flag:-q",
        "flag:-v",
        "path_prefix:/home/liara/workspace",
    ),
    "g": (
        "path_prefix:/tmp",
    ),
    "b": (
        "path_prefix:/etc",
        "path_prefix:/root",
        "path_prefix:/proc",
        "path_prefix:/sys",
        "path_prefix:/dev",
        "path_prefix:/mnt",
        "path_prefix:/media",
        "path_prefix:/home/liara/.ssh",
        "path_prefix:/home/liara/.gnupg",
        "path_prefix:/home/liara/.config",
    ),
}

# ── controlled write command policies ───────────────────────────────────────
_MKDIR_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-p",
        "path_prefix:/home/liara/workspace",
        "path_prefix:/home/liara/temp",
    ),
    "g": (),
    "b": (
        "path_prefix:/etc",
        "path_prefix:/root",
        "path_prefix:/proc",
        "path_prefix:/sys",
        "path_prefix:/dev",
        "path_prefix:/mnt",
        "path_prefix:/media",
    ),
}

_CP_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-r", "flag:-R", "flag:-n", "flag:-f", "flag:-a",
        "path_prefix:/home/liara",
    ),
    "g": (),
    "b": (
        "path_prefix:/etc",
        "path_prefix:/root",
        "path_prefix:/proc",
        "path_prefix:/sys",
        "path_prefix:/dev",
        "path_prefix:/mnt",
        "path_prefix:/media",
        "path_prefix:/home/liara/.ssh",
        "path_prefix:/home/liara/.gnupg",
        "path_prefix:/home/liara/.config",
    ),
}

_MV_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-n", "flag:-f",
        "path_prefix:/home/liara/workspace",
    ),
    "g": (),
    "b": (
        "path_prefix:/etc",
        "path_prefix:/root",
        "path_prefix:/proc",
        "path_prefix:/sys",
        "path_prefix:/dev",
        "path_prefix:/mnt",
        "path_prefix:/media",
    ),
}

_TOUCH_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-c", "flag:-a", "flag:-m",
        "path_prefix:/home/liara/workspace",
        "path_prefix:/home/liara/temp",
    ),
    "g": (),
    "b": (
        "path_prefix:/etc",
        "path_prefix:/root",
        "path_prefix:/proc",
        "path_prefix:/sys",
        "path_prefix:/dev",
        "path_prefix:/mnt",
        "path_prefix:/media",
    ),
}


_HIGH_RISK_BLOCKED_COMMANDS: frozenset[str] = frozenset(
    {
        "rm", "chmod", "chown", "mount", "su", "setpriv", "ip", "iptables", "nc", "ssh", "wget"
    }
)

# ── date / time policy ────────────────────────────────────────────────────────
# date and time are read-only system info commands; minimal flags needed
_DATE_TIME_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:+%Y-%m-%d",   # date format string
        "flag:+%H:%M:%S",   # time format string
        "flag:+%Y-%m-%d %H:%M:%S",  # datetime format
        "flag:+%s",         # unix timestamp
        "flag:+%Z",         # timezone
        "flag:+%z",         # timezone offset
        "flag:+%a",         # abbreviated weekday
        "flag:+%A",         # full weekday
        "flag:+%b",         # abbreviated month
        "flag:+%B",         # full month
        "flag:+%I",         # hour (12-hour)
        "flag:+%p",         # AM/PM
        "flag:-u",          # UTC
        "flag:-R",          # RFC 2822 format
        "flag:-I",          # ISO 8601 format
    ),
    "g": (),
    "b": (
        "flag:-s",          # set system time (dangerous)
        "flag:-d",          # date parsing (contextual)
    ),
}

_CURL_POLICY_DEFAULTS: dict[str, tuple[str, ...]] = {
    "w": (
        "flag:-s", "flag:--silent",
        "flag:-S", "flag:--show-error",
        "flag:-I", "flag:--head",
        "flag:-L", "flag:--location",
        "flag:-v", "flag:--verbose",
        "flag:--compressed",
        "flag:-A", "flag:--user-agent",
        "header_name:accept",
        "header_name:accept-encoding",
        "header_name:accept-language",
        "header_name:content-type",
        "header_name:user-agent",
        "header_name:cache-control",
        "header_name:x-request-id",
    ),
    "g": (
        "flag:-m", "flag:--max-time",
        "flag:-H", "flag:--header",
    ),
    "b": (
        "flag:-d", "flag:--data", "flag:--data-raw", "flag:--data-binary", "flag:--data-ascii", "flag:--data-urlencode",
        "flag:-F", "flag:--form", "flag:--form-string",
        "flag:-T", "flag:--upload-file",
        "flag:--json",
        "flag:-X", "flag:--request",
        "flag:-o", "flag:--output",
        "flag:-O", "flag:--remote-name",
        "flag:--remote-name-all",
        "flag:-u", "flag:--user",
        "flag:-n", "flag:--netrc",
        "flag:--netrc-file",
        "flag:--netrc-optional",
        "flag:--oauth2-bearer",
        "flag:--aws-sigv4",
        "flag:-b", "flag:--cookie",
        "flag:-c", "flag:--cookie-jar",
        "flag:-x", "flag:--proxy",
        "flag:--proxy-user",
        "flag:--socks4", "flag:--socks4a", "flag:--socks5", "flag:--socks5-hostname",
        "flag:--preproxy",
        "flag:-k", "flag:--insecure",
        "flag:--cacert", "flag:--capath",
        "flag:--cert", "flag:--key",
        "flag:--pinnedpubkey",
        "flag:-K", "flag:--config",
        "flag:-i", "flag:--include",
        "flag:--no-buffer",
        "flag:--max-redirs",
        "flag:-w", "flag:--write-out",
        "flag:--resolve",
        "flag:--connect-to",
        "flag:--interface",
        "flag:--dns-servers",
        "flag:--noproxy",
        "flag:--haproxy-protocol",
        "flag:--unix-socket",
        "flag:--abstract-unix-socket",
        "flag:--next",
        "flag:-:",
        "header_prefix:authorization:",
        "header_prefix:cookie:",
        "header_prefix:proxy-authorization:",
        "header_prefix:x-api-key:",
        "header_prefix:x-auth-token:",
        "header_prefix:www-authenticate:",
    ),
}


def list_profiled_command_names() -> frozenset[str]:
    """Return command names with dedicated policy profiles in this module."""
    return frozenset({
        "curl",
        "julia",
        "python3",
        "python",
        "venv-pip",
        "date",
        "time",
        "find",
        "cat",
        "ls",
        "grep",
        "head",
        "tail",
        "mkdir",
        "cp",
        "mv",
        "touch",
        "git",
        "tar",
        "jq",
        "tee",
    })


def list_high_risk_blocked_commands() -> frozenset[str]:
    """Return commands that are denied unconditionally by high-risk policy."""
    return _HIGH_RISK_BLOCKED_COMMANDS


@lru_cache(maxsize=16)
def _command_policy_sets(command_name: str) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    if command_name == "curl":
        policy = load_command_policy("curl", defaults=_CURL_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name == "julia":
        policy = load_command_policy("julia", defaults=_JULIA_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name in ("python3", "python"):
        policy = load_command_policy("python3", defaults=_PYTHON3_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name == "venv-pip":
        return frozenset(), frozenset(), frozenset()

    if command_name == "find":
        policy = load_command_policy("find", defaults=_FIND_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name == "cat":
        policy = load_command_policy("cat", defaults=_CAT_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name == "ls":
        policy = load_command_policy("ls", defaults=_LS_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name == "grep":
        policy = load_command_policy("grep", defaults=_GREP_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name in ("head", "tail"):
        policy = load_command_policy(command_name, defaults=_HEAD_TAIL_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name == "mkdir":
        policy = load_command_policy("mkdir", defaults=_MKDIR_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name == "cp":
        policy = load_command_policy("cp", defaults=_CP_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name == "mv":
        policy = load_command_policy("mv", defaults=_MV_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name == "touch":
        policy = load_command_policy("touch", defaults=_TOUCH_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name in ("date", "time"):
        policy = load_command_policy(command_name, defaults=_DATE_TIME_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name == "jq":
        policy = load_command_policy("jq", defaults=_JQ_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    if command_name == "tee":
        policy = load_command_policy("tee", defaults=_TEE_POLICY_DEFAULTS)
        return policy.whitelist, policy.greylist, policy.blacklist

    policy = load_command_policy(command_name, defaults={"w": (), "g": (), "b": ()})
    return policy.whitelist, policy.greylist, policy.blacklist


def check_command_policy(command: str) -> PolicyResult:
    """Validate a full shell command against command-specific policy profiles."""
    try:
        tokens = shlex.split(command)
    except ValueError as e:
        return PolicyResult(allowed=False, error=f"Cannot parse command: {e}", error_type="parse_error")

    if not tokens:
        return PolicyResult(allowed=False, error="Empty command", error_type="parse_error")

    command_name = tokens[0].rsplit("/", 1)[-1]
    policy_class = _detect_policy_class(command_name, tokens)
    if command_name in _HIGH_RISK_BLOCKED_COMMANDS:
        return PolicyResult(
            allowed=False,
            error=f"Command '{command_name}' is blocked by high-risk policy.",
            error_type="blocked_command",
            policy_class=policy_class,
        )
    if command_name == "curl":
        result = _check_curl_tokens(tokens)
        result.policy_class = policy_class
        return result
    if command_name == "julia":
        result = _check_julia_tokens(tokens)
        result.policy_class = policy_class
        return result
    if command_name in ("python3", "python"):
        result = _check_python3_tokens(tokens)
        result.policy_class = policy_class
        return result
    if command_name == "venv-pip":
        return _check_venv_pip_tokens(tokens)
    if command_name == "find":
        return _check_find_tokens(tokens)
    if command_name == "cat":
        return _check_cat_tokens(tokens)
    if command_name == "ls":
        return _check_ls_tokens(tokens)
    if command_name == "grep":
        return _check_grep_tokens(tokens)
    if command_name == "head":
        return _check_head_tail_tokens(tokens, command_name="head")
    if command_name == "tail":
        return _check_head_tail_tokens(tokens, command_name="tail")
    if command_name in ("date", "time"):
        return _check_date_time_tokens(tokens, command_name="date")
    if command_name == "mkdir":
        return _check_mkdir_tokens(tokens)
    if command_name == "cp":
        return _check_cp_mv_tokens(tokens, command_name="cp")
    if command_name == "mv":
        return _check_cp_mv_tokens(tokens, command_name="mv")
    if command_name == "touch":
        return _check_touch_tokens(tokens)
    if command_name == "git":
        return _check_git_tokens(tokens)
    if command_name == "tar":
        return _check_tar_tokens(tokens)
    if command_name == "jq":
        return _check_jq_tokens(tokens)
    if command_name == "tee":
        return _check_tee_tokens(tokens)
    # Default for non-profiled commands: no additional checks at this layer.
    return PolicyResult(allowed=True, policy_class=policy_class)


def check_command_request(command: str, args: list[str] | None = None) -> PolicyResult:
    """Validate a structured /sys request (`command` + `args`)."""
    command_name = str(command).strip().rsplit("/", 1)[-1]
    if not command_name:
        return PolicyResult(allowed=False, error="Empty command", error_type="parse_error")

    policy_class = _detect_policy_class(command_name, [command_name, *[str(a) for a in (args or [])]])

    if command_name in _HIGH_RISK_BLOCKED_COMMANDS:
        return PolicyResult(
            allowed=False,
            error=f"Command '{command_name}' is blocked by high-risk policy.",
            error_type="blocked_command",
            policy_class=policy_class,
        )

    arg_list = [str(a) for a in (args or [])]
    if command_name == "curl":
        result = _check_curl_tokens([command_name, *arg_list])
        result.policy_class = policy_class
        return result
    if command_name == "julia":
        result = _check_julia_tokens([command_name, *arg_list])
        result.policy_class = policy_class
        return result
    if command_name in ("python3", "python"):
        result = _check_python3_tokens([command_name, *arg_list])
        result.policy_class = policy_class
        return result
    if command_name == "venv-pip":
        return _check_venv_pip_tokens([command_name, *arg_list])
    if command_name == "find":
        return _check_find_tokens([command_name, *arg_list])
    if command_name == "cat":
        return _check_cat_tokens([command_name, *arg_list])
    if command_name == "ls":
        return _check_ls_tokens([command_name, *arg_list])
    if command_name == "grep":
        return _check_grep_tokens([command_name, *arg_list])
    if command_name == "head":
        return _check_head_tail_tokens([command_name, *arg_list], command_name="head")
    if command_name == "tail":
        return _check_head_tail_tokens([command_name, *arg_list], command_name="tail")
    if command_name in ("date", "time"):
        return _check_date_time_tokens([command_name, *arg_list], command_name="date")
    if command_name == "mkdir":
        return _check_mkdir_tokens([command_name, *arg_list])
    if command_name == "cp":
        return _check_cp_mv_tokens([command_name, *arg_list], command_name="cp")
    if command_name == "mv":
        return _check_cp_mv_tokens([command_name, *arg_list], command_name="mv")
    if command_name == "touch":
        return _check_touch_tokens([command_name, *arg_list])
    if command_name == "git":
        return _check_git_tokens([command_name, *arg_list])
    if command_name == "tar":
        return _check_tar_tokens([command_name, *arg_list])
    if command_name == "jq":
        return _check_jq_tokens([command_name, *arg_list])
    if command_name == "tee":
        return _check_tee_tokens([command_name, *arg_list])
    # Default for non-profiled commands: no additional checks at this layer.
    return PolicyResult(allowed=True, policy_class=policy_class)


def _check_curl_tokens(tokens: list[str]) -> PolicyResult:
    whitelist, greylist, blacklist = _command_policy_sets("curl")

    urls_found: list[str] = []
    i = 1
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"

        if flag_key in blacklist:
            return PolicyResult(
                allowed=False,
                error=f"Flag '{token}' is not permitted (write/upload/auth/proxy/insecure).",
                error_type="blocked_flag",
            )

        if token in ("-H", "--header"):
            if flag_key not in greylist and flag_key not in whitelist:
                return PolicyResult(
                    allowed=False,
                    error=f"Flag '{token}' is not in the policy allowlist.",
                    error_type="unknown_flag",
                )
            i += 1
            if i >= len(tokens):
                return PolicyResult(allowed=False, error="Missing value after -H/--header", error_type="blocked_flag")
            header_val = tokens[i]
            lower = header_val.lower()
            blocked_prefixes = [
                entry.split(":", 1)[1]
                for entry in blacklist
                if entry.startswith("header_prefix:")
            ]
            for blocked in blocked_prefixes:
                if lower.startswith(blocked):
                    return PolicyResult(
                        allowed=False,
                        error=f"Header '{header_val}' is not permitted.",
                        error_type="blocked_header",
                    )
            header_name = lower.split(":")[0].strip()
            if f"header_name:{header_name}" not in whitelist:
                return PolicyResult(
                    allowed=False,
                    error=f"Header '{header_name}' is not in the allowed header list.",
                    error_type="blocked_header",
                )
            i += 1
            continue

        if token in ("-A", "--user-agent"):
            # User-agent string: consume next token as value, no further validation.
            i += 1
            if i >= len(tokens):
                return PolicyResult(allowed=False, error="Missing value after -A/--user-agent", error_type="blocked_flag")
            i += 1  # skip the user-agent string value
            continue

        if token in ("-m", "--max-time"):
            if flag_key not in greylist and flag_key not in whitelist:
                return PolicyResult(
                    allowed=False,
                    error=f"Flag '{token}' is not in the policy allowlist.",
                    error_type="unknown_flag",
                )
            i += 1
            if i >= len(tokens):
                return PolicyResult(allowed=False, error="Missing value after -m/--max-time", error_type="blocked_flag")
            val = tokens[i]
            try:
                float(val)
            except ValueError:
                return PolicyResult(allowed=False, error=f"Invalid value for -m: '{val}'", error_type="blocked_flag")
            i += 1
            continue

        if token.startswith("-"):
            if _is_combined_short_flag(token):
                i += 1
                continue
            if flag_key not in whitelist:
                return PolicyResult(
                    allowed=False,
                    error=f"Flag '{token}' is unknown and not permitted (unknown = deny).",
                    error_type="unknown_flag",
                )
            i += 1
            continue

        if _looks_like_any_url(token):
            parsed = urlparse(token)
            if parsed.scheme not in ("http", "https"):
                return PolicyResult(
                    allowed=False,
                    error=f"URL scheme '{parsed.scheme}' is not permitted. Only http/https allowed.",
                    error_type="url_error",
                )
            urls_found.append(token)
            i += 1
            continue

        return PolicyResult(
            allowed=False,
            error=f"Unexpected token '{token}' in curl command.",
            error_type="unknown_flag",
        )

    if len(urls_found) == 0:
        return PolicyResult(allowed=False, error="No URL found in curl command.", error_type="url_error")
    if len(urls_found) > 1:
        return PolicyResult(
            allowed=False,
            error=f"Only one URL is permitted, found {len(urls_found)}: {urls_found}",
            error_type="url_error",
        )

    parsed = urlparse(urls_found[0])
    if not parsed.netloc:
        return PolicyResult(allowed=False, error=f"Invalid URL (no host): '{urls_found[0]}'", error_type="url_error")

    return PolicyResult(allowed=True)


def _is_combined_short_flag(token: str) -> bool:
    allowed_short: frozenset[str] = frozenset("sSILv")
    if not token.startswith("-") or token.startswith("--"):
        return False
    chars = token[1:]
    return len(chars) > 0 and all(c in allowed_short for c in chars)


def _looks_like_any_url(token: str) -> bool:
    return "://" in token


# ── julia checker ────────────────────────────────────────────────────────────

_JULIA_SAFE_PATH_PREFIXES: tuple[str, ...] = (
    "/home/liara/workspace",
    "/home/liara/temp",
)


def _check_julia_tokens(tokens: list[str]) -> PolicyResult:
    whitelist, greylist, blacklist = _command_policy_sets("julia")
    del greylist

    safe_path_prefixes = tuple(
        entry.split(":", 1)[1]
        for entry in whitelist
        if entry.startswith("path_prefix:")
    ) or _JULIA_SAFE_PATH_PREFIXES
    blocked_path_prefixes = tuple(
        entry.split(":", 1)[1]
        for entry in blacklist
        if entry.startswith("path_prefix:")
    )

    script_path: str | None = None
    version_requested = False
    i = 1
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"

        if flag_key in blacklist:
            return PolicyResult(
                allowed=False,
                error=f"julia flag '{token}' is not permitted.",
                error_type="blocked_flag",
            )

        if token.startswith("-"):
            if flag_key not in whitelist:
                return PolicyResult(
                    allowed=False,
                    error=f"julia flag '{token}' is unknown and not permitted.",
                    error_type="unknown_flag",
                )
            if token in {"--version", "-v"}:
                version_requested = True
            i += 1
            continue

        if script_path is not None:
            return PolicyResult(
                allowed=False,
                error=f"julia positional argument '{token}' is not permitted after the script path.",
                error_type="blocked_arg",
            )

        if not token.endswith(".jl"):
            return PolicyResult(
                allowed=False,
                error=f"julia script '{token}' must end with .jl.",
                error_type="blocked_path",
            )

        for blocked in blocked_path_prefixes:
            if token.startswith(blocked):
                return PolicyResult(
                    allowed=False,
                    error=f"julia script path '{token}' is not permitted (blocked prefix: {blocked}).",
                    error_type="blocked_path",
                )

        if not any(token.startswith(prefix) for prefix in safe_path_prefixes):
            return PolicyResult(
                allowed=False,
                error=(
                    f"julia script path '{token}' is outside permitted roots "
                    f"({', '.join(safe_path_prefixes)})."
                ),
                error_type="blocked_path",
            )

        script_path = token
        i += 1

    if script_path is None and not version_requested:
        return PolicyResult(
            allowed=False,
            error="julia requires either --version or a .jl script path inside the LIARA workspace.",
            error_type="blocked_arg",
        )

    return PolicyResult(allowed=True)


# ── python3 checker ───────────────────────────────────────────────────────────

def _check_python3_tokens(tokens: list[str]) -> PolicyResult:
    """Validate python3 / python args against the python3 policy DB.

    Permitted call shapes (from orchestrator):
        python3 -c <code>
        python3 -m pytest [safe flags] tests[/selector]

    Rules enforced from blacklist:
    - Blocked flags (e.g. -i interactive, -- end-of-options)
    - Blocked imports: os, sys, subprocess, socket, urllib, requests, …
    - Blocked builtins in code: open(, exec(, eval(, compile(, __import__, breakpoint
    """
    _whitelist, _greylist, blacklist = _command_policy_sets("python3")
    whitelist, greylist, _ = _command_policy_sets("python3")

    code_block: str | None = None
    i = 1
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"

        if flag_key in blacklist:
            return PolicyResult(
                allowed=False,
                error=f"python3 flag '{token}' is not permitted.",
                error_type="blocked_flag",
            )

        if token == "-c":
            if flag_key not in whitelist:
                return PolicyResult(
                    allowed=False,
                    error="python3 flag '-c' is not in whitelist.",
                    error_type="unknown_flag",
                )
            i += 1
            if i >= len(tokens):
                return PolicyResult(
                    allowed=False,
                    error="Missing code argument after -c.",
                    error_type="blocked_flag",
                )
            code_block = tokens[i]
            i += 1
            continue

        if token == "-m":
            if flag_key not in greylist and flag_key not in whitelist:
                return PolicyResult(
                    allowed=False,
                    error="python3 flag '-m' is not permitted.",
                    error_type="blocked_flag",
                )
            # -m is greylisted: only allow a small set of safe stdlib modules
            _ALLOWED_MODULES = frozenset({"math", "decimal", "fractions", "statistics",
                                          "datetime", "calendar", "uuid", "hashlib",
                                          "base64", "json", "csv", "textwrap", "pprint",
                                          "pytest", "services.simulation.bridge"})
            i += 1
            if i >= len(tokens):
                return PolicyResult(
                    allowed=False,
                    error="Missing module name after -m.",
                    error_type="blocked_flag",
                )
            module = tokens[i]
            if module not in _ALLOWED_MODULES:
                return PolicyResult(
                    allowed=False,
                    error=f"python3 -m '{module}' is not in the allowed module list.",
                    error_type="blocked_flag",
                )
            i += 1
            if module == "pytest":
                return _check_pytest_module_args(tokens[i:])
            continue

        if token.startswith("-"):
            if flag_key not in whitelist and flag_key not in greylist:
                return PolicyResult(
                    allowed=False,
                    error=f"python3 flag '{token}' is unknown and not permitted.",
                    error_type="unknown_flag",
                )
            i += 1
            continue

        # Non-flag positional: only allowed after -c (handled above) or as script path (blocked)
        return PolicyResult(
            allowed=False,
            error=f"python3: positional argument '{token}' is not permitted (no script execution, use -c).",
            error_type="blocked_flag",
        )

    # Validate inline code against blacklist patterns, but avoid string-literal
    # false positives by parsing Python AST where possible.
    if code_block is not None:
        blocked_imports = {
            entry.split(":", 1)[1].strip().lower()
            for entry in blacklist
            if entry.startswith("import:")
        }
        blocked_calls = {
            entry.split(":", 1)[1].strip().lower().rstrip("(")
            for entry in blacklist
            if entry.startswith("call:")
        }
        parsed = _parse_python_ast(code_block)
        if parsed is not None:
            imported_modules = _collect_python_import_roots(parsed)
            for module in sorted(imported_modules):
                if module in blocked_imports:
                    return PolicyResult(
                        allowed=False,
                        error=f"python3 code contains blocked import '{module}'.",
                        error_type="blocked_code",
                    )

            called_functions = _collect_python_called_roots(parsed)
            for func in sorted(called_functions):
                if func in blocked_calls:
                    return PolicyResult(
                        allowed=False,
                        error=f"python3 code contains blocked call '{func}'.",
                        error_type="blocked_code",
                    )
        else:
            # Conservative fallback if AST parse fails.
            blocked_patterns = [
                entry.split(":", 1)[1]
                for entry in blacklist
                if entry.startswith("import:") or entry.startswith("call:")
            ]
            for pattern in blocked_patterns:
                if re.search(r'\b' + re.escape(pattern.rstrip("(").split(":")[-1]) + r'\b', code_block):
                    return PolicyResult(
                        allowed=False,
                        error=f"python3 code contains blocked construct '{pattern}'.",
                        error_type="blocked_code",
                    )

    return PolicyResult(allowed=True)


def _check_pytest_module_args(args: list[str]) -> PolicyResult:
    """Permit deterministic tests without opening general Python execution.

    Test selectors must remain relative to the workspace ``tests`` directory.
    Options which can load plugins, replace configuration, or redirect pytest
    outside that tree are intentionally not part of this surface.
    """
    if len(args) > 16:
        return PolicyResult(False, "pytest accepts at most 16 arguments.", "blocked_argument")

    selectors = 0
    for token in args:
        if token.startswith("-"):
            if token not in _PYTEST_SAFE_FLAGS:
                return PolicyResult(
                    False,
                    f"pytest flag '{token}' is not permitted.",
                    "blocked_flag",
                )
            continue
        if not _PYTEST_SELECTOR_RE.fullmatch(token):
            return PolicyResult(
                False,
                f"pytest selector '{token}' must be a relative path below tests/.",
                "blocked_path",
            )
        selectors += 1

    if selectors == 0:
        return PolicyResult(
            False,
            "pytest requires an explicit relative selector below tests/.",
            "blocked_path",
        )
    return PolicyResult(True, policy_class="test-execution")


def _check_venv_pip_tokens(tokens: list[str]) -> PolicyResult:
    """Allow a narrow, non-interactive install/show surface in workspace .venv."""
    if len(tokens) < 3:
        return PolicyResult(False, "venv-pip requires an action and package names.", "blocked_argument")
    action = tokens[1]
    args = tokens[2:]
    if action not in {"install", "show"}:
        return PolicyResult(False, f"venv-pip action '{action}' is not permitted.", "blocked_argument")

    required_flags = {"--disable-pip-version-check", "--no-input"}
    if action == "install":
        supplied_flags = {item for item in args if item.startswith("-")}
        if not required_flags.issubset(supplied_flags):
            return PolicyResult(False, "venv-pip install requires --disable-pip-version-check and --no-input.", "blocked_flag")
        packages = [item for item in args if not item.startswith("-")]
    else:
        if any(item.startswith("-") for item in args):
            return PolicyResult(False, "venv-pip show does not accept flags.", "blocked_flag")
        packages = args

    if not packages or len(packages) > 8:
        return PolicyResult(False, "venv-pip permits between 1 and 8 packages.", "blocked_argument")
    allowlist = _dependency_allowlist()
    for spec in packages:
        if not _DEPENDENCY_SPEC_RE.fullmatch(spec):
            return PolicyResult(False, f"dependency spec '{spec}' is not permitted.", "blocked_dependency")
        name = _dependency_name(spec)
        if name not in allowlist:
            return PolicyResult(False, f"dependency '{name}' requires explicit allowlist approval.", "blocked_dependency")
    return PolicyResult(True, policy_class="environment-mutation")


def _detect_policy_class(command_name: str, tokens: list[str]) -> str:
    if command_name == "julia":
        return "language-bridge"
    if command_name in {"python3", "python"} and len(tokens) >= 3 and tokens[1:3] == ["-m", "pytest"]:
        return "test-execution"
    if command_name in {"python3", "python"} and _python_tokens_indicate_bridge(tokens):
        return "language-bridge"
    return "standard"


def _python_tokens_indicate_bridge(tokens: list[str]) -> bool:
    if len(tokens) < 3:
        return False
    if tokens[1] != "-c":
        return False
    code = tokens[2]
    return bool(re.search(r"\b(pyjulia|ctypes|libjulia|julia)\b", code, re.IGNORECASE))


def _parse_python_ast(code: str) -> ast.AST | None:
    try:
        return ast.parse(code)
    except SyntaxError:
        return None


def _collect_python_import_roots(tree: ast.AST) -> set[str]:
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0].lower()
                imports.add(root)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root = node.module.split(".", 1)[0].lower()
                imports.add(root)
    return imports


def _collect_python_called_roots(tree: ast.AST) -> set[str]:
    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            calls.add(func.id.lower())
        elif isinstance(func, ast.Attribute):
            calls.add(func.attr.lower())
    return calls


# ── find checker ──────────────────────────────────────────────────────────────

_FIND_ALLOWED_FLAGS: frozenset[str] = frozenset({
    "-maxdepth", "-mindepth", "-type", "-name", "-iname",
    "-size", "-newer", "-mtime", "-atime", "-print", "-print0",
})
_FIND_BLOCKED_FLAGS: frozenset[str] = frozenset({
    "-exec", "-execdir", "-ok", "-delete", "-ls",
})
_FIND_BLOCKED_PATH_PREFIXES: tuple[str, ...] = (
    "/etc", "/root", "/proc", "/sys", "/dev", "/mnt", "/media",
)
# Only workspace paths are unconditionally safe
_FIND_SAFE_PATH_PREFIXES: tuple[str, ...] = ("/home/liara/workspace", "/tmp")


def _check_find_tokens(tokens: list[str]) -> PolicyResult:
    """Validate find args: path must be under /home/liara/workspace, no -exec/-delete."""
    whitelist, greylist, blacklist = _command_policy_sets("find")

    # Reload blocked prefixes from DB in case they were edited
    blocked_path_prefixes = tuple(
        entry.split(":", 1)[1]
        for entry in blacklist
        if entry.startswith("path:")
    ) or _FIND_BLOCKED_PATH_PREFIXES

    paths_found: list[str] = []
    i = 1
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"

        if flag_key in blacklist or token in _FIND_BLOCKED_FLAGS:
            return PolicyResult(
                allowed=False,
                error=f"find flag '{token}' is not permitted.",
                error_type="blocked_flag",
            )

        if token in _FIND_ALLOWED_FLAGS or flag_key in whitelist or flag_key in greylist:
            # Flags with a following value argument
            if token in ("-maxdepth", "-mindepth", "-type", "-name", "-iname",
                         "-size", "-newer", "-mtime", "-atime"):
                i += 1
                if i >= len(tokens):
                    return PolicyResult(
                        allowed=False,
                        error=f"find flag '{token}' requires a value.",
                        error_type="blocked_flag",
                    )
            i += 1
            continue

        if token.startswith("-"):
            return PolicyResult(
                allowed=False,
                error=f"find flag '{token}' is unknown and not permitted.",
                error_type="unknown_flag",
            )

        # Positional: path argument
        for blocked in blocked_path_prefixes:
            if token.startswith(blocked):
                return PolicyResult(
                    allowed=False,
                    error=f"find path '{token}' is not permitted (blocked prefix: {blocked}).",
                    error_type="blocked_path",
                )
        if not any(token.startswith(safe) for safe in _FIND_SAFE_PATH_PREFIXES):
            return PolicyResult(
                allowed=False,
                error=f"find path '{token}' is outside permitted search roots ({', '.join(_FIND_SAFE_PATH_PREFIXES)}).",
                error_type="blocked_path",
            )
        paths_found.append(token)
        i += 1

    if not paths_found:
        return PolicyResult(
            allowed=False,
            error="find requires at least one path argument.",
            error_type="blocked_flag",
        )

    return PolicyResult(allowed=True)


# ── cat checker ───────────────────────────────────────────────────────────────

_CAT_ALLOWED_FLAGS: frozenset[str] = frozenset({"-n", "-b", "-s", "-A", "-v"})
_CAT_BLOCKED_PATH_PREFIXES: tuple[str, ...] = (
    "/etc", "/root", "/proc", "/sys", "/dev", "/mnt", "/media",
    "/home/liara/.ssh", "/home/liara/.gnupg", "/home/liara/.config",
)
_CAT_SAFE_PATH_PREFIXES: tuple[str, ...] = ("/home/liara",)


def _check_cat_tokens(tokens: list[str]) -> PolicyResult:
    """Validate cat: allow read-only flags, restrict paths to safe prefixes."""
    whitelist, greylist, blacklist = _command_policy_sets("cat")

    blocked_path_prefixes = tuple(
        entry.split(":", 1)[1]
        for entry in blacklist
        if entry.startswith("path_prefix:")
    ) or _CAT_BLOCKED_PATH_PREFIXES

    safe_path_prefixes = (
        tuple(
            entry.split(":", 1)[1]
            for entry in whitelist | greylist
            if entry.startswith("path_prefix:")
        )
        or _CAT_SAFE_PATH_PREFIXES
    )

    paths_found: list[str] = []
    i = 1
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"

        if flag_key in blacklist:
            return PolicyResult(
                allowed=False,
                error=f"cat flag '{token}' is not permitted.",
                error_type="blocked_flag",
            )

        if token in _CAT_ALLOWED_FLAGS or flag_key in whitelist:
            i += 1
            continue

        if token.startswith("-"):
            return PolicyResult(
                allowed=False,
                error=f"cat flag '{token}' is unknown and not permitted.",
                error_type="unknown_flag",
            )

        # Positional: file path
        for blocked in blocked_path_prefixes:
            if token.startswith(blocked):
                return PolicyResult(
                    allowed=False,
                    error=f"cat path '{token}' is not permitted (blocked prefix: {blocked}).",
                    error_type="blocked_path",
                )
        if not any(token.startswith(safe) for safe in safe_path_prefixes):
            return PolicyResult(
                allowed=False,
                error=f"cat path '{token}' is outside permitted read roots ({', '.join(safe_path_prefixes)}).",
                error_type="blocked_path",
            )
        paths_found.append(token)
        i += 1

    if not paths_found:
        return PolicyResult(
            allowed=False,
            error="cat requires at least one file path argument.",
            error_type="blocked_flag",
        )

    return PolicyResult(allowed=True)


# ── ls / grep / head / tail checkers ────────────────────────────────────────

_COMMON_BLOCKED_PATH_PREFIXES: tuple[str, ...] = (
    "/etc", "/root", "/proc", "/sys", "/dev", "/mnt", "/media",
    "/home/liara/.ssh", "/home/liara/.gnupg", "/home/liara/.config",
)
_COMMON_SAFE_PATH_PREFIXES: tuple[str, ...] = ("/home/liara",)


def _load_path_prefix_policy(command_name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    whitelist, greylist, blacklist = _command_policy_sets(command_name)

    blocked = tuple(
        entry.split(":", 1)[1]
        for entry in blacklist
        if entry.startswith("path_prefix:")
    ) or _COMMON_BLOCKED_PATH_PREFIXES

    safe = tuple(
        entry.split(":", 1)[1]
        for entry in whitelist | greylist
        if entry.startswith("path_prefix:")
    ) or _COMMON_SAFE_PATH_PREFIXES

    return blocked, safe


def _validate_path_token(path_token: str, *, command_name: str, blocked: tuple[str, ...], safe: tuple[str, ...]) -> PolicyResult | None:
    for blocked_prefix in blocked:
        if path_token.startswith(blocked_prefix):
            return PolicyResult(
                allowed=False,
                error=f"{command_name} path '{path_token}' is not permitted (blocked prefix: {blocked_prefix}).",
                error_type="blocked_path",
            )
    if not any(path_token.startswith(prefix) for prefix in safe):
        return PolicyResult(
            allowed=False,
            error=f"{command_name} path '{path_token}' is outside permitted roots ({', '.join(safe)}).",
            error_type="blocked_path",
        )
    return None


def _check_ls_tokens(tokens: list[str]) -> PolicyResult:
    whitelist, greylist, blacklist = _command_policy_sets("ls")
    blocked_paths, safe_paths = _load_path_prefix_policy("ls")

    i = 1
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"

        if flag_key in blacklist:
            return PolicyResult(allowed=False, error=f"ls flag '{token}' is not permitted.", error_type="blocked_flag")

        if token.startswith("-"):
            if _is_ls_combined_short_flag(token):
                i += 1
                continue
            if flag_key not in whitelist and flag_key not in greylist:
                return PolicyResult(allowed=False, error=f"ls flag '{token}' is unknown and not permitted.", error_type="unknown_flag")
            i += 1
            continue

        invalid = _validate_path_token(token, command_name="ls", blocked=blocked_paths, safe=safe_paths)
        if invalid is not None:
            return invalid
        i += 1

    return PolicyResult(allowed=True)


def _is_ls_combined_short_flag(token: str) -> bool:
    if not token.startswith("-") or token.startswith("--"):
        return False
    chars = token[1:]
    return len(chars) > 1 and all(c in {"1", "a", "A", "l", "h", "R", "t", "S"} for c in chars)


def _check_jq_tokens(tokens: list[str]) -> PolicyResult:
    whitelist, greylist, blacklist = _command_policy_sets("jq")
    blocked_paths, safe_paths = _load_path_prefix_policy("jq")

    i = 1
    filter_seen = False
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"

        if flag_key in blacklist:
            return PolicyResult(allowed=False, error=f"jq flag '{token}' is not permitted.", error_type="blocked_flag")

        if token.startswith("-"):
            if flag_key not in whitelist and flag_key not in greylist:
                return PolicyResult(allowed=False, error=f"jq flag '{token}' is unknown and not permitted.", error_type="unknown_flag")
            # flags that consume a following value argument
            if token in ("-f", "--from-file", "--arg", "--argjson"):
                i += 1
                if i >= len(tokens):
                    return PolicyResult(allowed=False, error=f"jq flag '{token}' requires a value.", error_type="blocked_flag")
                # for --arg / --argjson the next two tokens are name + value (both consumed)
                if token in ("--arg", "--argjson"):
                    i += 1
                    if i >= len(tokens):
                        return PolicyResult(allowed=False, error=f"jq flag '{token}' requires name and value arguments.", error_type="blocked_flag")
            i += 1
            continue

        if not filter_seen:
            # first non-flag argument is the jq filter expression (free text, not validated)
            filter_seen = True
            i += 1
            continue

        # subsequent non-flag arguments are input file paths
        invalid = _validate_path_token(token, command_name="jq", blocked=blocked_paths, safe=safe_paths)
        if invalid is not None:
            return invalid
        i += 1

    return PolicyResult(allowed=True)


def _check_tee_tokens(tokens: list[str]) -> PolicyResult:
    whitelist, greylist, blacklist = _command_policy_sets("tee")
    blocked_paths, safe_paths = _load_path_prefix_policy("tee")

    target_path: str | None = None
    append_mode = False
    i = 1
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"

        if flag_key in blacklist:
            return PolicyResult(allowed=False, error=f"tee flag '{token}' is not permitted.", error_type="blocked_flag")

        if token.startswith("-"):
            if flag_key not in whitelist and flag_key not in greylist:
                return PolicyResult(allowed=False, error=f"tee flag '{token}' is unknown and not permitted.", error_type="unknown_flag")
            if token in ("-a", "--append"):
                if append_mode:
                    return PolicyResult(allowed=False, error="tee append mode may only be specified once.", error_type="blocked_flag")
                append_mode = True
                i += 1
                continue
            return PolicyResult(allowed=False, error=f"tee flag '{token}' is not enabled in the controlled write profile.", error_type="blocked_flag")

        if target_path is not None:
            return PolicyResult(allowed=False, error="tee supports exactly one target path in the initial write profile.", error_type="blocked_flag")

        invalid = _validate_path_token(token, command_name="tee", blocked=blocked_paths, safe=safe_paths)
        if invalid is not None:
            return invalid
        target_path = token
        i += 1

    if target_path is None:
        return PolicyResult(allowed=False, error="tee requires exactly one target path argument.", error_type="blocked_flag")

    return PolicyResult(allowed=True)


def _check_grep_tokens(tokens: list[str]) -> PolicyResult:
    whitelist, greylist, blacklist = _command_policy_sets("grep")
    blocked_paths, safe_paths = _load_path_prefix_policy("grep")

    i = 1
    pattern_seen = False
    file_count = 0
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"

        if flag_key in blacklist:
            return PolicyResult(allowed=False, error=f"grep flag '{token}' is not permitted.", error_type="blocked_flag")

        if token.startswith("-"):
            if flag_key not in whitelist and flag_key not in greylist:
                return PolicyResult(allowed=False, error=f"grep flag '{token}' is unknown and not permitted.", error_type="unknown_flag")
            if token in ("-m",):
                i += 1
                if i >= len(tokens):
                    return PolicyResult(allowed=False, error="grep flag '-m' requires a value.", error_type="blocked_flag")
            i += 1
            continue

        if not pattern_seen:
            pattern_seen = True
            i += 1
            continue

        invalid = _validate_path_token(token, command_name="grep", blocked=blocked_paths, safe=safe_paths)
        if invalid is not None:
            return invalid
        file_count += 1
        i += 1

    if not pattern_seen:
        return PolicyResult(allowed=False, error="grep requires a pattern argument.", error_type="blocked_flag")
    if file_count == 0:
        return PolicyResult(allowed=False, error="grep requires at least one file path argument.", error_type="blocked_flag")
    return PolicyResult(allowed=True)


def _check_head_tail_tokens(tokens: list[str], *, command_name: str) -> PolicyResult:
    whitelist, greylist, blacklist = _command_policy_sets(command_name)
    blocked_paths, safe_paths = _load_path_prefix_policy(command_name)

    i = 1
    file_count = 0
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"

        if flag_key in blacklist:
            return PolicyResult(allowed=False, error=f"{command_name} flag '{token}' is not permitted.", error_type="blocked_flag")

        if token.startswith("-"):
            if flag_key not in whitelist and flag_key not in greylist:
                return PolicyResult(allowed=False, error=f"{command_name} flag '{token}' is unknown and not permitted.", error_type="unknown_flag")
            if token in ("-n", "-c"):
                i += 1
                if i >= len(tokens):
                    return PolicyResult(allowed=False, error=f"{command_name} flag '{token}' requires a value.", error_type="blocked_flag")
            i += 1
            continue

        invalid = _validate_path_token(token, command_name=command_name, blocked=blocked_paths, safe=safe_paths)
        if invalid is not None:
            return invalid
        file_count += 1
        i += 1

    if file_count == 0:
        return PolicyResult(allowed=False, error=f"{command_name} requires at least one file path argument.", error_type="blocked_flag")
    return PolicyResult(allowed=True)


def _check_date_time_tokens(tokens: list[str], *, command_name: str) -> PolicyResult:
    """Check date/time command tokens.
    
    Date/time commands are read-only and mostly harmless; mainly check for
    obvious blacklist flags like -s (set system time).
    """
    whitelist, greylist, blacklist = _command_policy_sets(command_name)
    
    # Scan for blacklist flags (e.g., -s to set time)
    for token in tokens[1:]:
        if token.startswith("-s"):
            return PolicyResult(
                allowed=False,
                error=f"Flag '{token}' (set time) is not permitted.",
                error_type="blocked_flag",
            )
    
    # Everything else is allowed for date/time
    return PolicyResult(allowed=True)


def _workspace_prefixes_for(command_name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    blocked, safe = _load_path_prefix_policy(command_name)
    return blocked, safe


def _check_mkdir_tokens(tokens: list[str]) -> PolicyResult:
    whitelist, greylist, blacklist = _command_policy_sets("mkdir")
    blocked_paths, safe_paths = _workspace_prefixes_for("mkdir")
    paths = 0
    i = 1
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"
        if token.startswith("-"):
            if flag_key in blacklist:
                return PolicyResult(False, f"mkdir flag '{token}' is not permitted.", "blocked_flag")
            if flag_key not in whitelist and flag_key not in greylist:
                return PolicyResult(False, f"mkdir flag '{token}' is unknown and not permitted.", "unknown_flag")
            i += 1
            continue
        invalid = _validate_path_token(token, command_name="mkdir", blocked=blocked_paths, safe=safe_paths)
        if invalid is not None:
            return invalid
        paths += 1
        i += 1
    if paths == 0:
        return PolicyResult(False, "mkdir requires at least one target path.", "blocked_flag")
    return PolicyResult(True)


def _check_cp_mv_tokens(tokens: list[str], *, command_name: str) -> PolicyResult:
    whitelist, greylist, blacklist = _command_policy_sets(command_name)
    blocked_paths, safe_paths = _workspace_prefixes_for(command_name)
    paths: list[str] = []
    i = 1
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"
        if token.startswith("-"):
            if flag_key in blacklist:
                return PolicyResult(False, f"{command_name} flag '{token}' is not permitted.", "blocked_flag")
            if flag_key not in whitelist and flag_key not in greylist:
                return PolicyResult(False, f"{command_name} flag '{token}' is unknown and not permitted.", "unknown_flag")
            i += 1
            continue
        invalid = _validate_path_token(token, command_name=command_name, blocked=blocked_paths, safe=safe_paths)
        if invalid is not None:
            return invalid
        paths.append(token)
        i += 1
    if len(paths) < 2:
        return PolicyResult(False, f"{command_name} requires source and destination paths.", "blocked_flag")
    return PolicyResult(True)


def _check_touch_tokens(tokens: list[str]) -> PolicyResult:
    whitelist, greylist, blacklist = _command_policy_sets("touch")
    blocked_paths, safe_paths = _workspace_prefixes_for("touch")
    paths = 0
    i = 1
    while i < len(tokens):
        token = tokens[i]
        flag_key = f"flag:{token}"
        if token.startswith("-"):
            if flag_key in blacklist:
                return PolicyResult(False, f"touch flag '{token}' is not permitted.", "blocked_flag")
            if flag_key not in whitelist and flag_key not in greylist:
                return PolicyResult(False, f"touch flag '{token}' is unknown and not permitted.", "unknown_flag")
            i += 1
            continue
        invalid = _validate_path_token(token, command_name="touch", blocked=blocked_paths, safe=safe_paths)
        if invalid is not None:
            return invalid
        paths += 1
        i += 1
    if paths == 0:
        return PolicyResult(False, "touch requires at least one file path.", "blocked_flag")
    return PolicyResult(True)


def _git_allowed_hosts() -> tuple[str, ...]:
    raw = os.getenv("LIARA_GIT_URL_ALLOWLIST", "github.com,gitlab.com")
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def _check_git_tokens(tokens: list[str]) -> PolicyResult:
    if len(tokens) < 3:
        return PolicyResult(False, "git command too short; only restricted operations are allowed.", "blocked_flag")
    subcmd = tokens[1]
    if subcmd not in {"clone", "ls-remote"}:
        return PolicyResult(False, f"git subcommand '{subcmd}' is not permitted.", "blocked_flag")

    url_token = next((t for t in tokens[2:] if "://" in t or t.startswith("git@")), None)
    if not url_token:
        return PolicyResult(False, "git requires a remote URL argument.", "blocked_flag")

    host = ""
    if url_token.startswith("git@"):
        host = url_token.split("@", 1)[1].split(":", 1)[0].lower()
    else:
        parsed = urlparse(url_token)
        host = (parsed.hostname or "").lower()
    if not host:
        return PolicyResult(False, "git URL host could not be parsed.", "blocked_flag")

    if not any(host == allowed or host.endswith(f".{allowed}") for allowed in _git_allowed_hosts()):
        return PolicyResult(False, f"git host '{host}' is not in URL allowlist.", "blocked_host")

    if subcmd == "clone":
        # Require explicit destination path and keep it inside workspace.
        if len(tokens) < 4:
            return PolicyResult(False, "git clone requires explicit destination path in workspace.", "blocked_path")
        dest = tokens[-1]
        invalid = _validate_path_token(dest, command_name="git", blocked=_COMMON_BLOCKED_PATH_PREFIXES, safe=("/home/liara/workspace",))
        if invalid is not None:
            return invalid

    return PolicyResult(True)


def _check_tar_tokens(tokens: list[str]) -> PolicyResult:
    if len(tokens) < 3:
        return PolicyResult(False, "tar command too short.", "blocked_flag")

    safe_paths = ("/home/liara/workspace",)
    blocked_paths = _COMMON_BLOCKED_PATH_PREFIXES

    i = 1
    while i < len(tokens):
        token = tokens[i]
        if token == "-C":
            i += 1
            if i >= len(tokens):
                return PolicyResult(False, "tar flag '-C' requires a directory path.", "blocked_flag")
            invalid = _validate_path_token(tokens[i], command_name="tar", blocked=blocked_paths, safe=safe_paths)
            if invalid is not None:
                return invalid
            i += 1
            continue

        if token.startswith("-"):
            # Allow common tar mode bundles only.
            if not re.match(r"^-[ctxzvf]+$", token):
                return PolicyResult(False, f"tar flag '{token}' is not permitted.", "blocked_flag")
            i += 1
            continue

        invalid = _validate_path_token(token, command_name="tar", blocked=blocked_paths, safe=safe_paths)
        if invalid is not None:
            return invalid
        i += 1

    return PolicyResult(True)
