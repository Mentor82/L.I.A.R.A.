"""Built-in: WSL Executor — runs commands in the isolated WSL environment."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import shlex
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
import time

from ..base import Tool
from .policy_db import list_policy_commands
from .sys_command_policy import check_command_policy, check_command_request, list_profiled_command_names
from .sys_audit import log_blocked, log_executed, log_started
from services.api.exceptions import AuditPersistenceError
from services.simulation.wsl_session_runtime import WslSessionError, WslSessionManager


# ---------------------------------------------------------------------------
# Security policy: available base commands are derived from policy DB entries.
# ---------------------------------------------------------------------------


def _allowed_commands() -> frozenset[str]:
    commands = set(list_policy_commands())
    commands.update(list_profiled_command_names())
    commands.add("health")

    # python and python3 should be interchangeable for caller convenience.
    if "python3" in commands:
        commands.add("python")
    if "python" in commands:
        commands.add("python3")

    return frozenset(commands)

# Commands that are never permitted regardless of context
_BLOCKED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+-rf\b"),
    re.compile(r"\bmkfs\b"),
    re.compile(r"\bdd\b"),
    re.compile(r"\bshutdown\b"),
    re.compile(r"\breboot\b"),
    re.compile(r"\bpoweroff\b"),
    re.compile(r"\bwget\b"),
    re.compile(r"\bssh\b"),
    re.compile(r"\bchmod\s+[0-7]*7\b"),  # world-write
    re.compile(r"\bchown\b"),
    re.compile(r"\bsudo\b"),
    re.compile(r"\bsu\b"),
    re.compile(r"\bdoas\b"),
    re.compile(r">\s*/etc/"),         # redirect into /etc
    re.compile(r">\s*/home/(?!liara/)"),  # write outside liara home
]

_WSL_DISTRO = os.getenv("LIARA_WSL_DISTRO", "Debian")
_WSL_USER = "liara"
_DEFAULT_WORKDIR = "/home/liara/workspace"
_DEFAULT_TIMEOUT = 30
_DEFAULT_MAX_STDIN_BYTES = 64 * 1024
_JULIA_DISCOVERY_TIMEOUT_SECONDS = 5
_STDIN_ENABLED_COMMANDS: frozenset[str] = frozenset({"julia", "tee"})
_STRUCTURED_WRITE_COMMANDS: frozenset[str] = frozenset({"tee", "mkdir", "touch"})
_TMP_RETENTION_SECONDS = 24 * 60 * 60
_JULIA_PATH_ENV = "LIARA_WSL_JULIA_PATH"
_SYS_HEALTH_URL_ENV = "LIARA_SYS_HEALTH_URL"
_SYS_HEALTH_BACKENDS_URL_ENV = "LIARA_SYS_HEALTH_BACKENDS_URL"
_SYS_HEARTBEAT_URL_ENV = "LIARA_SYS_HEARTBEAT_URL"
_DEFAULT_SYS_HEALTH_URL = "http://127.0.0.1:8010/health"
_DEFAULT_SYS_HEALTH_BACKENDS_URL = "http://127.0.0.1:8010/health/backends"
_DEFAULT_SYS_HEARTBEAT_URL = "http://127.0.0.1:8010/operations/heartbeat?window_seconds=60"

_MUTATION_VERIFY_SCRIPT = r"""
import hashlib
import json
import os
import sys

path, command, write_mode, expected_sha, expected_size_raw = sys.argv[1:]
expected_size = int(expected_size_raw)
result = {
    "target_path": path,
    "command": command,
    "write_mode": write_mode,
    "exists": os.path.exists(path),
    "kind": "missing",
    "size_bytes": None,
    "sha256": None,
    "content_match": None,
    "verified": False,
}

if os.path.isdir(path):
    result["kind"] = "directory"
    result["verified"] = command == "mkdir"
elif os.path.isfile(path):
    result["kind"] = "file"
    result["size_bytes"] = os.path.getsize(path)
    with open(path, "rb") as handle:
        payload = handle.read()
    result["sha256"] = hashlib.sha256(payload).hexdigest()
    if command == "tee" and write_mode == "append":
        tail = payload[-expected_size:] if expected_size else b""
        result["content_match"] = hashlib.sha256(tail).hexdigest() == expected_sha
        result["verified"] = bool(result["content_match"])
    elif command == "tee":
        result["content_match"] = result["sha256"] == expected_sha
        result["verified"] = bool(result["content_match"])
    else:
        result["verified"] = command == "touch"

print(json.dumps(result, sort_keys=True))
raise SystemExit(0 if result["verified"] else 3)
""".strip()


def _resolve_wsl_executable() -> str | None:
    """Resolve wsl executable path robustly across task/service environments."""
    direct = shutil.which("wsl")
    if direct:
        return direct

    windir = os.environ.get("WINDIR") or r"C:\Windows"
    candidates = [
        os.path.join(windir, "System32", "wsl.exe"),
        r"C:\Windows\System32\wsl.exe",
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


async def _run_wsl_exec_probe(
    argv: list[str],
    *,
    timeout: int,
) -> tuple[int, bytes, bytes]:
    """Run a small argument-safe WSL control command."""
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            ),
            timeout=timeout,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return int(proc.returncode or 0), stdout or b"", stderr or b""
    except NotImplementedError:
        completed = await asyncio.to_thread(
            subprocess.run,
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
        return int(completed.returncode), completed.stdout or b"", completed.stderr or b""


async def _ensure_workspace_venv(
    *,
    wsl_executable: str,
    execution_user: str,
    workdir: str,
    timeout: int,
) -> dict[str, Any]:
    """Create the confined workspace venv when its pip executable is absent."""
    venv_root = str(PurePosixPath(workdir) / ".venv")
    pip_path = str(PurePosixPath(venv_root) / "bin" / "pip")
    common = [
        wsl_executable,
        "-d", _WSL_DISTRO,
        "-u", execution_user,
        "--cd", workdir,
        "--exec",
    ]
    probe_code, _, _ = await _run_wsl_exec_probe(
        [*common, "test", "-x", pip_path],
        timeout=max(1, min(timeout, 10)),
    )
    if probe_code == 0:
        return {"venv_bootstrapped": False, "venv_root": venv_root, "pip_path": pip_path}

    create_code, create_stdout, create_stderr = await _run_wsl_exec_probe(
        [*common, "python3", "-m", "venv", ".venv"],
        timeout=timeout,
    )
    if create_code != 0:
        detail = create_stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"workspace venv bootstrap failed with code {create_code}"
            + (f": {detail}" if detail else "")
        )
    return {
        "venv_bootstrapped": True,
        "venv_root": venv_root,
        "pip_path": pip_path,
        "bootstrap_stdout": create_stdout.decode("utf-8", errors="replace"),
    }


async def _verify_wsl_mutation(
    *,
    wsl_executable: str,
    execution_user: str,
    workdir: str,
    target_path: str,
    command_name: str,
    write_mode: str | None,
    stdin_text: str | None,
    timeout: int,
) -> dict[str, Any]:
    """Read the mutation target back from WSL and return verifiable evidence."""
    input_bytes = (stdin_text or "").encode("utf-8")
    expected_sha = hashlib.sha256(input_bytes).hexdigest()
    verify_args = [
        wsl_executable,
        "-d", _WSL_DISTRO,
        "-u", execution_user,
        "--cd", workdir,
        "--",
        "python3", "-c", _MUTATION_VERIFY_SCRIPT,
        target_path,
        command_name,
        write_mode or "",
        expected_sha,
        str(len(input_bytes)),
    ]
    try:
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *verify_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=timeout,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            returncode = proc.returncode
        except NotImplementedError:
            completed = await asyncio.to_thread(
                subprocess.run,
                verify_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
            stdout_bytes = completed.stdout or b""
            stderr_bytes = completed.stderr or b""
            returncode = int(completed.returncode)
    except (asyncio.TimeoutError, subprocess.TimeoutExpired) as exc:
        if "proc" in locals() and proc is not None and proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except (ProcessLookupError, OSError):
                pass
        if os.name == "nt" and write_mode != "append":
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        _verify_host_unc_mutation,
                        target_path=target_path,
                        command_name=command_name,
                        write_mode=write_mode,
                        stdin_text=stdin_text,
                    ),
                    timeout=max(1, min(timeout, 5)),
                )
            except (asyncio.TimeoutError, OSError, RuntimeError, ValueError):
                pass
        raise RuntimeError("post-write verification timed out") from exc

    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
    try:
        evidence = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"post-write verification returned invalid evidence: {stderr or stdout}") from exc
    if returncode != 0 or not evidence.get("verified"):
        raise RuntimeError(
            f"post-write verification failed for {target_path}: {stderr or evidence}"
        )
    evidence["verification"] = "wsl_read_after_write"
    return evidence


def _verify_host_unc_mutation(
    *,
    target_path: str,
    command_name: str,
    write_mode: str | None,
    stdin_text: str | None,
) -> dict[str, Any]:
    """Reconcile a WSL mutation through its host UNC view after WSL timeout."""
    posix_target = PurePosixPath(target_path)
    allowed_root = PurePosixPath("/home/liara")
    try:
        relative = posix_target.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("UNC verification target is outside /home/liara") from exc

    unc_target = Path(rf"\\wsl.localhost\{_WSL_DISTRO}\home\liara").joinpath(*relative.parts)
    exists = unc_target.exists()
    evidence: dict[str, Any] = {
        "target_path": target_path,
        "command": command_name,
        "write_mode": write_mode,
        "exists": exists,
        "kind": "directory" if unc_target.is_dir() else "file" if unc_target.is_file() else "missing",
        "verified": False,
        "verification": "host_unc_read_after_write_fallback",
    }
    if command_name == "mkdir":
        evidence["verified"] = exists and unc_target.is_dir()
    elif command_name == "touch":
        evidence["verified"] = exists and unc_target.is_file()
    elif command_name == "tee" and write_mode != "append" and unc_target.is_file():
        expected_bytes = (stdin_text or "").encode("utf-8")
        digest = hashlib.sha256()
        size = 0
        with unc_target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        actual_sha = digest.hexdigest()
        expected_sha = hashlib.sha256(expected_bytes).hexdigest()
        evidence.update({
            "size_bytes": size,
            "sha256": actual_sha,
            "expected_sha256": expected_sha,
            "content_match": size == len(expected_bytes) and actual_sha == expected_sha,
        })
        evidence["verified"] = bool(evidence["content_match"])
    if not evidence["verified"]:
        raise RuntimeError(f"UNC post-write verification failed for {target_path}")
    return evidence


def _policy_check(command: str, args: list[str] | None = None) -> str | None:
    """Return an error string if command violates policy, else None."""
    command_line = _compose_command_line(command=command, args=args)

    for pattern in _BLOCKED_PATTERNS:
        if pattern.search(command_line):
            return f"Command blocked by security policy: matched '{pattern.pattern}'"

    # Extract the leading token (first word) and check against allowlist
    first_token = command.strip().split()[0] if command.strip() else ""
    # Strip path prefix if any (e.g. /usr/bin/python3 → python3)
    base = first_token.rsplit("/", 1)[-1]

    if base == "sys":
        tokens = command.strip().split()
        if args is None and len(tokens) == 2 and tokens[1].strip().lower() == "health":
            health_url = (os.getenv(_SYS_HEALTH_URL_ENV) or _DEFAULT_SYS_HEALTH_URL).strip() or _DEFAULT_SYS_HEALTH_URL
            result = check_command_request("curl", ["-sS", health_url])
            if not result.allowed:
                return f"health policy violation [{result.error_type}]: {result.error}"
            return None
        if isinstance(args, list) and len(args) == 1 and str(args[0]).strip().lower() == "health":
            health_url = (os.getenv(_SYS_HEALTH_URL_ENV) or _DEFAULT_SYS_HEALTH_URL).strip() or _DEFAULT_SYS_HEALTH_URL
            result = check_command_request("curl", ["-sS", health_url])
            if not result.allowed:
                return f"health policy violation [{result.error_type}]: {result.error}"
            return None

    if base == "health":
        if args not in (None, []):
            return "Command 'health' does not accept args. Usage: /sys health"
        health_url = (os.getenv(_SYS_HEALTH_URL_ENV) or _DEFAULT_SYS_HEALTH_URL).strip() or _DEFAULT_SYS_HEALTH_URL
        result = check_command_request("curl", ["-sS", health_url])
        if not result.allowed:
            return f"health policy violation [{result.error_type}]: {result.error}"
        return None

    allowed_commands = _allowed_commands()
    if base not in allowed_commands:
        return (
            f"Command '{base}' is not in the allowed command list. "
            f"Allowed: {', '.join(sorted(allowed_commands))}"
        )

    # command-specific policy checks (e.g. curl profile)
    if base:
        if args is not None:
            result = check_command_request(base, args)
        else:
            result = check_command_policy(command)
        if not result.allowed:
            return f"{base} policy violation [{result.error_type}]: {result.error}"

    return None


def _compose_command_line(command: str, args: list[str] | None = None) -> str:
    if not args:
        return command
    return " ".join([shlex.quote(command), *[shlex.quote(str(a)) for a in args]])


def _replace_command_token(command: str, executable_path: str) -> str:
    """Replace the first command token with a resolved executable path."""
    stripped = command.strip()
    if not stripped:
        return command

    token = stripped.split()[0]
    if stripped == token:
        return executable_path
    return command.replace(token, executable_path, 1)


def _default_julia_path() -> str:
    return f"/home/{_WSL_USER}/.juliaup/bin/julia"


async def _discover_julia_path_in_wsl(
    *,
    wsl_executable: str,
    workdir: str,
    timeout: int,
) -> str | None:
    """Discover julia executable inside WSL via `command -v julia`."""
    wsl_args = [
        wsl_executable,
        "-d", _WSL_DISTRO,
        "-u", _WSL_USER,
        "--cd", workdir,
        "--",
        "sh", "-lc", "command -v julia",
    ]

    try:
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    *wsl_args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=timeout,
            )
            stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            returncode = proc.returncode
        except NotImplementedError:
            completed = await asyncio.to_thread(
                subprocess.run,
                wsl_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
                check=False,
            )
            stdout_bytes = completed.stdout or b""
            returncode = int(completed.returncode)
    except Exception:
        return None

    if returncode != 0:
        return None

    discovered = (stdout_bytes.decode("utf-8", errors="replace") or "").strip().splitlines()
    if not discovered:
        return None

    candidate = discovered[0].strip()
    if candidate.startswith("/"):
        return candidate
    return None


async def _resolve_julia_command_for_wsl(
    *,
    command: str,
    args: list[str] | None,
    wsl_executable: str,
    workdir: str,
    timeout: int,
) -> tuple[str, dict[str, Any]]:
    """Resolve julia path with deterministic fallback order and telemetry."""
    env_override = (os.getenv(_JULIA_PATH_ENV) or "").strip()
    if env_override:
        if args is not None:
            resolved_command = env_override
        else:
            resolved_command = _replace_command_token(command, env_override)
        return resolved_command, {
            "strategy": "env_override",
            "fallback_used": False,
            "resolved_path": env_override,
            "env_var": _JULIA_PATH_ENV,
        }

    discovery_timeout = max(1, min(timeout, _JULIA_DISCOVERY_TIMEOUT_SECONDS))
    discovered = await _discover_julia_path_in_wsl(
        wsl_executable=wsl_executable,
        workdir=workdir,
        timeout=discovery_timeout,
    )
    if discovered:
        if args is not None:
            resolved_command = discovered
        else:
            resolved_command = _replace_command_token(command, discovered)
        return resolved_command, {
            "strategy": "wsl_command_v",
            "fallback_used": True,
            "resolved_path": discovered,
            "env_var": _JULIA_PATH_ENV,
        }

    default_path = _default_julia_path()
    if args is not None:
        resolved_command = default_path
    else:
        resolved_command = _replace_command_token(command, default_path)
    return resolved_command, {
        "strategy": "default_juliaup",
        "fallback_used": True,
        "resolved_path": default_path,
        "env_var": _JULIA_PATH_ENV,
    }


class WslExecutorTool(Tool):
    """Execute a shell command inside the isolated WSL environment.

    The command runs as user 'liara' in /home/liara/workspace with no
    access to /mnt/c (Windows automount disabled in wsl.conf).

    Security: v0.0.2 enforces a command allowlist, blocks dangerous
    patterns, and applies flag-level curl policy (read-only http/https only).

    V0.0.5 adds structured /sys input (`command` + `args`) as preferred mode.
    Legacy single-string command input is still supported for compatibility.
    """

    @property
    def name(self) -> str:
        return "sys"

    @property
    def description(self) -> str:
        return (
            "Run a shell command inside the isolated WSL environment "
            "(user: liara, workdir: /home/liara/workspace)"
        )

    @property
    def required_parameters(self) -> list[str]:
        return ["command"]

    @property
    def optional_parameters(self) -> list[str]:
        return [
            "args",
            "stdin_text",
            "stdin_transport",
            "timeout",
            "workdir",
            "workspace_session_id",
            "request_id",
            "session_id",
            "run_id",
            "source",
            "context",
            "proposal_id",
            # Selector/executor metadata fields (non-execution-critical).
            "url",
            "search_query",
            "target_path",
            "storage_scope",
            "write_mode",
            "retention_hint_seconds",
        ]

    async def execute(self, **kwargs: Any) -> dict[str, Any]:
        self._validate_parameters(**kwargs)

        command: str = kwargs["command"]
        args_raw = kwargs.get("args")
        stdin_text_raw = kwargs.get("stdin_text")
        stdin_transport = str(kwargs.get("stdin_transport") or "auto").strip().lower()
        timeout: int = int(kwargs.get("timeout", _DEFAULT_TIMEOUT))
        workdir: str = str(kwargs.get("workdir", _DEFAULT_WORKDIR))
        workspace_session_id: str | None = kwargs.get("workspace_session_id")
        execution_user = _WSL_USER
        session_execution_context: dict[str, str] | None = None
        request_id: str | None = kwargs.get("request_id")
        session_id: str | None = kwargs.get("session_id")
        run_id: str | None = kwargs.get("run_id")
        source: str | None = kwargs.get("source")
        context_val: str | None = kwargs.get("context")
        proposal_id: str | None = kwargs.get("proposal_id")

        if stdin_transport not in {"auto", "threaded"}:
            return self.failure("'stdin_transport' must be 'auto' or 'threaded'.")

        if workspace_session_id:
            try:
                session_execution_context = WslSessionManager().execution_context(workspace_session_id)
            except (WslSessionError, OSError, ValueError) as exc:
                return self.failure(f"WSL session context rejected: {exc}")
            session_root = PurePosixPath(session_execution_context["workdir"])
            if "workdir" in kwargs:
                requested_workdir = PurePosixPath(workdir)
                try:
                    requested_workdir.relative_to(session_root)
                except ValueError:
                    return self.failure("workdir is outside the selected WSL session work tree")
            else:
                workdir = str(session_root)
            execution_user = session_execution_context["execution_user"]

        args: list[str] | None = None
        if args_raw is not None:
            if not isinstance(args_raw, list) or not all(isinstance(a, str) for a in args_raw):
                return self.failure("'args' must be a list[str] when provided.")
            if any("\n" in a for a in args_raw):
                return self.failure("'args' must not contain newline characters.")
            if " " in command.strip():
                return self.failure("When 'args' is used, 'command' must be a bare executable name/path.")
            args = list(args_raw)

        stdin_text: str | None = None
        if stdin_text_raw is not None:
            if not isinstance(stdin_text_raw, str):
                return self.failure("'stdin_text' must be a string when provided.")
            stdin_bytes = stdin_text_raw.encode("utf-8")
            if len(stdin_bytes) > _DEFAULT_MAX_STDIN_BYTES:
                return self.failure(
                    f"'stdin_text' exceeds the { _DEFAULT_MAX_STDIN_BYTES } byte limit.",
                    metadata={"stdin_bytes": len(stdin_bytes)},
                )
            stdin_text = stdin_text_raw

        command_name = command.strip().split()[0].rsplit("/", 1)[-1] if command.strip() else ""
        if command_name == "sys":
            tokens = command.strip().split()
            if args is None and len(tokens) == 2 and tokens[1].strip().lower() == "health":
                command = "health"
                command_name = "health"
            elif isinstance(args, list) and len(args) == 1 and str(args[0]).strip().lower() == "health":
                command = "health"
                args = []
                command_name = "health"

        health_url = None
        health_backends_url = None
        heartbeat_url = None
        if command_name == "health":
            if args is not None and len(args) > 0:
                return self.failure("Command 'health' does not accept args. Usage: /sys health")
            if stdin_text is not None:
                return self.failure("'stdin_text' is not supported for command 'health'.")
            health_url = (os.getenv(_SYS_HEALTH_URL_ENV) or _DEFAULT_SYS_HEALTH_URL).strip() or _DEFAULT_SYS_HEALTH_URL
            health_backends_url = (
                os.getenv(_SYS_HEALTH_BACKENDS_URL_ENV) or _DEFAULT_SYS_HEALTH_BACKENDS_URL
            ).strip() or _DEFAULT_SYS_HEALTH_BACKENDS_URL
            heartbeat_url = (
                os.getenv(_SYS_HEARTBEAT_URL_ENV) or _DEFAULT_SYS_HEARTBEAT_URL
            ).strip() or _DEFAULT_SYS_HEARTBEAT_URL
            for probe_url in (health_url, health_backends_url, heartbeat_url):
                result = check_command_request("curl", ["-sS", probe_url])
                if not result.allowed:
                    return self.failure(f"health policy violation [{result.error_type}]: {result.error}")

        target_path = str(kwargs.get("target_path") or "").strip() or _extract_target_path(command_name, args)
        storage_scope = str(kwargs.get("storage_scope") or "").strip() or _classify_storage_scope(target_path)
        retention_hint_seconds = kwargs.get("retention_hint_seconds")
        if retention_hint_seconds is None:
            retention_hint_seconds = _retention_hint_seconds(storage_scope)
        write_mode = str(kwargs.get("write_mode") or "").strip() or _detect_write_mode(command_name, args)

        if stdin_text is not None and args is None:
            return self.failure("'stdin_text' requires structured mode ('command' + 'args') and is not allowed with shell-string execution.")
        if command_name in _STRUCTURED_WRITE_COMMANDS and args is None:
            return self.failure(
                f"Command '{command_name}' requires structured mode ('command' + 'args') and does not run via shell-string execution.",
            )
        if stdin_text is not None and command_name not in _STDIN_ENABLED_COMMANDS:
            return self.failure(
                f"'stdin_text' is not enabled for command '{command_name}'. Allowed: {', '.join(sorted(_STDIN_ENABLED_COMMANDS))}",
            )
        if command_name == "tee" and args is not None and stdin_text is None:
            return self.failure("tee requires 'stdin_text' in the initial controlled write profile.")

        # --- Safety: workdir must stay inside liara workspace ---
        if not workdir.startswith("/home/liara/"):
            return self.failure(
                f"workdir '{workdir}' is outside the allowed area (/home/liara/)."
            )

        # --- Policy check ---
        policy_error = _policy_check(command, args=args)
        if policy_error:
            log_blocked(
                command, args, policy_error,
                stdin_text=stdin_text,
                target_path=target_path,
                storage_scope=storage_scope,
                retention_hint_seconds=retention_hint_seconds,
                write_mode=write_mode,
                request_id=request_id, session_id=session_id, run_id=run_id, source=source, context=context_val,
                proposal_id=proposal_id,
            )
            return self.failure(policy_error)

        # Fail-closed pre-action audit: a durable "started" record must exist
        # before any external side effect (WSL subprocess spawn / health probe)
        # so a crash between here and the terminal log_executed() call is
        # visible as incomplete/outcome_unknown, never inferred as success.
        # No-op (returns an id, doesn't raise) when no repository is
        # configured -- see sys_audit.log_started's docstring.
        try:
            operation_id = log_started(
                command, args,
                target_path=target_path,
                storage_scope=storage_scope,
                write_mode=write_mode,
                request_id=request_id, session_id=session_id, run_id=run_id, source=source, context=context_val,
                proposal_id=proposal_id,
            )
        except AuditPersistenceError as exc:
            return self.failure(f"Audit persistence unavailable, refusing to execute: {exc}")

        if command_name == "health":
            _t_start = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    responses: dict[str, dict[str, Any]] = {}
                    for key, url in (
                        ("api_health", health_url),
                        ("memory_backends", health_backends_url),
                        ("heartbeat", heartbeat_url),
                    ):
                        try:
                            assert url is not None
                            response = await client.get(url)
                            response.raise_for_status()
                            try:
                                payload: Any = response.json()
                            except Exception:
                                payload = {"raw": response.text}
                            responses[key] = {
                                "status": "success",
                                "url": url,
                                "http_status": int(response.status_code),
                                "payload": payload,
                            }
                        except Exception as exc:
                            responses[key] = {
                                "status": "error",
                                "url": url,
                                "error": str(exc),
                            }

                api_payload = responses.get("api_health", {}).get("payload")
                if responses.get("api_health", {}).get("status") != "success":
                    raise RuntimeError(responses.get("api_health", {}).get("error") or "api health unavailable")

                backends_payload = responses.get("memory_backends", {}).get("payload")
                heartbeat_payload = responses.get("heartbeat", {}).get("payload")
                backend_health = (
                    backends_payload.get("backend_health", {})
                    if isinstance(backends_payload, dict)
                    else {}
                )
                heartbeat_curve = (
                    heartbeat_payload.get("curve", {})
                    if isinstance(heartbeat_payload, dict)
                    else {}
                )
                snapshot = {
                    "status": "success",
                    "api_health": api_payload,
                    "backend_health": backend_health,
                    "memory_backends": backends_payload,
                    "embedding_runtime": {
                        "device": backends_payload.get("device") if isinstance(backends_payload, dict) else None,
                        "execution_devices": backends_payload.get("execution_devices") if isinstance(backends_payload, dict) else None,
                        "runtime_backend": backends_payload.get("runtime_backend") if isinstance(backends_payload, dict) else None,
                        "model": backends_payload.get("model") if isinstance(backends_payload, dict) else None,
                        "dimensions": backends_payload.get("dimensions") if isinstance(backends_payload, dict) else None,
                    },
                    "heartbeat": {
                        "status": responses.get("heartbeat", {}).get("status"),
                        "service_health": (
                            heartbeat_payload.get("service_health")
                            if isinstance(heartbeat_payload, dict)
                            else None
                        ),
                        "state": heartbeat_curve.get("state") if isinstance(heartbeat_curve, dict) else None,
                        "trend": heartbeat_curve.get("trend") if isinstance(heartbeat_curve, dict) else None,
                        "stability": heartbeat_curve.get("stability") if isinstance(heartbeat_curve, dict) else None,
                        "confidence": heartbeat_curve.get("confidence") if isinstance(heartbeat_curve, dict) else None,
                        "envelope": heartbeat_curve.get("envelope") if isinstance(heartbeat_curve, dict) else None,
                    },
                    "probe_status": {
                        key: {
                            "status": value.get("status"),
                            "http_status": value.get("http_status"),
                            "error": value.get("error"),
                        }
                        for key, value in responses.items()
                    },
                }
                stdout = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
                _duration_ms = (time.monotonic() - _t_start) * 1000
                stdout_bytes = stdout.encode("utf-8", errors="replace")
                log_executed(
                    command,
                    args,
                    exit_code=0,
                    duration_ms=_duration_ms,
                    stdout_bytes=len(stdout_bytes),
                    stderr_bytes=0,
                    operation_id=operation_id,
                    stdin_text=stdin_text,
                    target_path=target_path,
                    storage_scope=storage_scope,
                    retention_hint_seconds=retention_hint_seconds,
                    write_mode=write_mode,
                    request_id=request_id,
                    session_id=session_id,
                    run_id=run_id,
                    source=source,
                    context=context_val,
                    proposal_id=proposal_id,
                )
                return self.success(
                    stdout,
                    metadata={
                        "command": f"GET {health_url}",
                        "health_urls": {
                            "api_health": health_url,
                            "memory_backends": health_backends_url,
                            "heartbeat": heartbeat_url,
                        },
                        "http_status": int(responses["api_health"]["http_status"]),
                        "probe_status": snapshot["probe_status"],
                        "duration_ms": round(_duration_ms, 2),
                        "target_path": target_path,
                        "storage_scope": storage_scope,
                        "retention_hint_seconds": retention_hint_seconds,
                        "write_mode": write_mode,
                        "stdin_bytes": 0,
                    },
                )
            except Exception as exc:
                _duration_ms = (time.monotonic() - _t_start) * 1000
                stderr = str(exc)
                stderr_bytes = stderr.encode("utf-8", errors="replace")
                log_executed(
                    command,
                    args,
                    exit_code=1,
                    duration_ms=_duration_ms,
                    stdout_bytes=0,
                    stderr_bytes=len(stderr_bytes),
                    operation_id=operation_id,
                    stdin_text=stdin_text,
                    target_path=target_path,
                    storage_scope=storage_scope,
                    retention_hint_seconds=retention_hint_seconds,
                    write_mode=write_mode,
                    request_id=request_id,
                    session_id=session_id,
                    run_id=run_id,
                    source=source,
                    context=context_val,
                    proposal_id=proposal_id,
                )
                return self.failure(
                    "Command exited with code 7.",
                    metadata={
                        "command": f"GET {health_url}",
                        "returncode": 7,
                        "stdout": "",
                        "stderr": stderr,
                        "julia_resolution": None,
                    },
                )

        # --- Check wsl.exe is available ---
        wsl_executable = _resolve_wsl_executable()
        if not wsl_executable:
            return self.failure("wsl.exe not found on PATH. Is WSL installed?")

        # Build subprocess args. Structured mode avoids shell-string execution.
        julia_resolution: dict[str, Any] | None = None
        venv_bootstrap: dict[str, Any] | None = None
        command_resolved = command
        if command_name == "julia":
            command_resolved, julia_resolution = await _resolve_julia_command_for_wsl(
                command=command,
                args=args,
                wsl_executable=wsl_executable,
                workdir=workdir,
                timeout=timeout,
            )
        elif command_name == "venv-pip":
            command_resolved = str(PurePosixPath(workdir) / ".venv" / "bin" / "pip")
            try:
                venv_bootstrap = await _ensure_workspace_venv(
                    wsl_executable=wsl_executable,
                    execution_user=execution_user,
                    workdir=workdir,
                    timeout=timeout,
                )
            except (asyncio.TimeoutError, subprocess.TimeoutExpired, RuntimeError, OSError) as exc:
                return self.failure(
                    str(exc),
                    metadata={
                        "command": command_resolved,
                        "venv_bootstrap": {"failed": True, "error": str(exc)},
                        "workdir": workdir,
                    },
                )
        elif command_name == "python":
            command_resolved = str(PurePosixPath(workdir) / ".venv" / "bin" / "python")
        if args is not None:
            wsl_args = [
                wsl_executable,
                "-d", _WSL_DISTRO,
                "-u", execution_user,
                "--cd", workdir,
                "--exec",
                command_resolved,
                *args,
            ]
            command_for_meta = _compose_command_line(command=command_resolved, args=args)
        else:
            wsl_args = [
                wsl_executable,
                "-d", _WSL_DISTRO,
                "-u", execution_user,
                "--cd", workdir,
                "--",
                "sh", "-c", command_resolved,
            ]
            command_for_meta = command_resolved

        _t_start = time.monotonic()
        returncode: int | None = None
        proc: asyncio.subprocess.Process | None = None
        try:
            stdin_pipe = asyncio.subprocess.PIPE if stdin_text is not None else None
            input_bytes = stdin_text.encode("utf-8") if stdin_text is not None else None
            try:
                if stdin_transport == "threaded":
                    completed = await asyncio.to_thread(
                        subprocess.run,
                        wsl_args,
                        input=input_bytes,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        timeout=timeout,
                        check=False,
                    )
                    stdout_bytes = completed.stdout or b""
                    stderr_bytes = completed.stderr or b""
                    returncode = int(completed.returncode)
                else:
                    proc = await asyncio.wait_for(
                        asyncio.create_subprocess_exec(
                            *wsl_args,
                            stdin=stdin_pipe,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        ),
                        timeout=timeout,
                    )
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(input=input_bytes), timeout=timeout
                    )
                    returncode = proc.returncode
            except NotImplementedError:
                # Some Windows event loop policies do not implement asyncio subprocess APIs.
                completed = await asyncio.to_thread(
                    subprocess.run,
                    wsl_args,
                    input=input_bytes,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                    check=False,
                )
                stdout_bytes = completed.stdout or b""
                stderr_bytes = completed.stderr or b""
                returncode = int(completed.returncode)
        except (asyncio.TimeoutError, subprocess.TimeoutExpired):
            if proc is not None and proc.returncode is None:
                try:
                    proc.kill()
                    await proc.wait()
                except (ProcessLookupError, OSError):
                    pass
            _duration_ms = (time.monotonic() - _t_start) * 1000
            log_executed(
                command,
                args,
                exit_code=124,
                duration_ms=_duration_ms,
                stdout_bytes=0,
                stderr_bytes=0,
                operation_id=operation_id,
                stdin_text=stdin_text,
                target_path=target_path,
                storage_scope=storage_scope,
                retention_hint_seconds=retention_hint_seconds,
                write_mode=write_mode,
                request_id=request_id,
                session_id=session_id,
                run_id=run_id,
                source=source,
                context=context_val,
                proposal_id=proposal_id,
            )

            # A timed-out WSL client may have completed an idempotent mutation
            # before its transport stalled. Reconcile the requested end state
            # before reporting failure or allowing a bounded retry.
            recovery_error: str | None = None
            if command_name in _STRUCTURED_WRITE_COMMANDS and target_path and write_mode != "append":
                try:
                    mutation_evidence = await _verify_wsl_mutation(
                        wsl_executable=wsl_executable,
                        execution_user=execution_user,
                        workdir=workdir,
                        target_path=target_path,
                        command_name=command_name,
                        write_mode=write_mode,
                        stdin_text=stdin_text,
                        timeout=max(1, min(timeout, 5)),
                    )
                    # Gap this closes: a timed-out-but-actually-succeeded
                    # mutation previously had no terminal audit event at all --
                    # only the exit_code=124 "started-then-timed-out" record
                    # above, never anything reflecting the reconciled success.
                    log_executed(
                        command,
                        args,
                        exit_code=0,
                        duration_ms=_duration_ms,
                        stdout_bytes=0,
                        stderr_bytes=0,
                        operation_id=operation_id,
                        stdin_text=stdin_text,
                        target_path=target_path,
                        storage_scope=storage_scope,
                        retention_hint_seconds=retention_hint_seconds,
                        write_mode=write_mode,
                        request_id=request_id,
                        session_id=session_id,
                        run_id=run_id,
                        source=source,
                        context=context_val,
                        proposal_id=proposal_id,
                    )
                    return self.success(
                        "",
                        metadata={
                            "command": command_for_meta,
                            "returncode": 124,
                            "timeout": timeout,
                            "target_path": target_path,
                            "storage_scope": storage_scope,
                            "write_mode": write_mode,
                            "mutation_verified": True,
                            "mutation_evidence": mutation_evidence,
                            "mutation_reconciled_after_timeout": True,
                            "transient_error": False,
                            "julia_resolution": julia_resolution,
                        },
                    )
                except (RuntimeError, OSError, ValueError) as exc:
                    recovery_error = str(exc)
            return self.failure(
                f"Command timed out after {timeout}s.",
                metadata={
                    "command": command_for_meta,
                    "timeout": timeout,
                    "target_path": target_path,
                    "mutation_verified": False,
                    "mutation_reconciled_after_timeout": False,
                    "transient_error": True,
                    "recovery_error": recovery_error,
                    "julia_resolution": julia_resolution,
                },
            )
        except Exception as exc:
            return self.failure(
                f"Failed to launch WSL process: {exc!r}",
                metadata={
                    "command": command_for_meta,
                    "julia_resolution": julia_resolution,
                },
            )

        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        if returncode is None:
            return self.failure(
                "WSL process did not report an exit code.",
                metadata={"command": command_for_meta},
            )
        _duration_ms = (time.monotonic() - _t_start) * 1000

        log_executed(
            command, args,
            exit_code=returncode,
            duration_ms=_duration_ms,
            stdout_bytes=len(stdout_bytes),
            stderr_bytes=len(stderr_bytes),
            operation_id=operation_id,
            stdin_text=stdin_text,
            target_path=target_path,
            storage_scope=storage_scope,
            retention_hint_seconds=retention_hint_seconds,
            write_mode=write_mode,
            request_id=request_id, session_id=session_id, run_id=run_id, source=source, context=context_val,
            proposal_id=proposal_id,
        )

        if returncode != 0:
            return self.failure(
                f"Command exited with code {returncode}.",
                metadata={
                    "command": command_for_meta,
                    "returncode": returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "julia_resolution": julia_resolution,
                    "venv_bootstrap": venv_bootstrap,
                },
            )

        mutation_evidence: dict[str, Any] | None = None
        if command_name in _STRUCTURED_WRITE_COMMANDS:
            if not target_path:
                return self.failure(
                    "Write command completed but no mutation target was available for verification.",
                    metadata={
                        "command": command_for_meta,
                        "returncode": returncode,
                        "mutation_verified": False,
                    },
                )
            try:
                mutation_evidence = await _verify_wsl_mutation(
                    wsl_executable=wsl_executable,
                    execution_user=execution_user,
                    workdir=workdir,
                    target_path=target_path,
                    command_name=command_name,
                    write_mode=write_mode,
                    stdin_text=stdin_text,
                    timeout=timeout,
                )
            except (RuntimeError, OSError, ValueError) as exc:
                return self.failure(
                    str(exc),
                    metadata={
                        "command": command_for_meta,
                        "returncode": returncode,
                        "stdout": stdout,
                        "stderr": stderr,
                        "target_path": target_path,
                        "mutation_verified": False,
                    },
                )

        return self.success(
            stdout,
            metadata={
                "command": command_for_meta,
                "returncode": returncode,
                "stderr": stderr,
                "workdir": workdir,
                "stdin_bytes": len(stdin_text.encode("utf-8")) if stdin_text is not None else 0,
                "target_path": target_path,
                "storage_scope": storage_scope,
                "retention_hint_seconds": retention_hint_seconds,
                "write_mode": write_mode,
                "mutation_verified": mutation_evidence is not None,
                "mutation_evidence": mutation_evidence,
                "julia_resolution": julia_resolution,
                "venv_bootstrap": venv_bootstrap,
                "workspace_session_id": workspace_session_id,
                "snapshot_hash": (
                    session_execution_context.get("snapshot_hash")
                    if session_execution_context else None
                ),
            },
        )


def _extract_target_path(command_name: str, args: Sequence[str] | None) -> str | None:
    if not args:
        return None
    if command_name == "tee":
        filtered = [arg for arg in args if not arg.startswith("-")]
        return filtered[0] if filtered else None
    if command_name == "julia":
        filtered = [arg for arg in args if not arg.startswith("-")]
        return filtered[0] if filtered else None
    if command_name in {"mkdir", "touch"}:
        filtered = [arg for arg in args if not arg.startswith("-")]
        return filtered[0] if filtered else None
    return None


def _classify_storage_scope(target_path: str | None) -> str | None:
    if target_path is None:
        return None
    if target_path.startswith("/home/liara/temp"):
        return "temp"
    if target_path.startswith("/home/liara/workspace"):
        return "workspace"
    return "external"


def _retention_hint_seconds(storage_scope: str | None) -> int | None:
    if storage_scope == "temp":
        return _TMP_RETENTION_SECONDS
    return None


def _detect_write_mode(command_name: str, args: Sequence[str] | None) -> str | None:
    if command_name == "tee" and args:
        if any(arg in {"-a", "--append"} for arg in args):
            return "append"
        return "overwrite"
    if command_name == "mkdir":
        return "mkdir"
    if command_name == "touch":
        return "touch"
    return None
