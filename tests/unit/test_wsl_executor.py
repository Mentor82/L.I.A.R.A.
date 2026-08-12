"""Unit tests for WslExecutorTool — no real WSL required (subprocess mocked)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.tools.builtin import sys_command_policy
from services.tools.builtin.policy_db import load_command_policy
from services.tools.builtin.wsl_executor import WslExecutorTool, _policy_check


@pytest.fixture(autouse=True)
def isolated_policy_db(tmp_path, monkeypatch):
    db_root = Path(tmp_path) / "db"
    monkeypatch.setenv("LIARA_POLICY_DB_DIR", str(db_root))

    # Create command DB folders so /sys availability is derived from DB entries.
    for command in ("python3", "curl", "find", "cat", "julia"):
        load_command_policy(command, defaults={"w": (), "g": (), "b": ()})

    sys_command_policy._command_policy_sets.cache_clear()
    yield
    sys_command_policy._command_policy_sets.cache_clear()


# ---------------------------------------------------------------------------
# Policy check tests (pure logic, no I/O)
# ---------------------------------------------------------------------------


class TestPolicyCheck:
    def test_allowed_command_passes(self):
        assert _policy_check("python3 -c 'print(1)'") is None

    def test_allowed_bare_name_passes(self):
        assert _policy_check("find /home/liara/workspace -maxdepth 1") is None

    def test_julia_version_allowed(self):
        assert _policy_check("julia --version") is None

    def test_julia_eval_blocked(self):
        err = _policy_check("julia -e 'println(1)'")
        assert err is not None

    def test_unknown_command_blocked(self):
        err = _policy_check("nmap 192.168.1.0/24")
        assert err is not None
        assert "nmap" in err

    def test_rm_rf_blocked(self):
        err = _policy_check("rm -rf /home/liara/workspace")
        assert err is not None

    def test_wget_blocked(self):
        err = _policy_check("wget http://example.com/evil.sh")
        assert err is not None

    def test_curl_allowed_by_default(self):
        # curl is now allowed but routed through flag-level policy
        err = _policy_check("curl https://example.com")
        assert err is None

    def test_curl_blocked_with_insecure_flag(self):
        err = _policy_check("curl -k https://example.com")
        assert err is not None

    def test_sudo_blocked(self):
        err = _policy_check("sudo rm file")
        assert err is not None

    def test_redirect_to_etc_blocked(self):
        err = _policy_check("echo x > /etc/passwd")
        assert err is not None

    def test_path_stripped_for_allowlist(self):
        # /usr/bin/python3 should resolve to python3 which is allowed
        assert _policy_check("/usr/bin/python3 -c 'print(1)'") is None

    def test_empty_command_blocked(self):
        err = _policy_check("")
        assert err is not None

    def test_structured_curl_allowed(self):
        err = _policy_check("curl", args=["-sI", "https://example.com"])
        assert err is None

    def test_structured_curl_blocked(self):
        err = _policy_check("curl", args=["-k", "https://example.com"])
        assert err is not None

    def test_structured_tee_allowed(self):
        err = _policy_check("tee", args=["/home/liara/workspace/report.txt"])
        assert err is None

    def test_structured_mkdir_allowed(self):
        err = _policy_check("mkdir", args=["-p", "/home/liara/temp/liara-out"])
        assert err is None

    def test_health_alias_allowed(self):
        assert _policy_check("health") is None

    def test_health_alias_rejects_args(self):
        err = _policy_check("health", args=["now"])
        assert err is not None
        assert "does not accept args" in err

    def test_legacy_sys_health_alias_allowed(self):
        assert _policy_check("sys health") is None

    def test_legacy_structured_sys_health_alias_allowed(self):
        assert _policy_check("sys", args=["health"]) is None


# ---------------------------------------------------------------------------
# Tool parameter validation
# ---------------------------------------------------------------------------


class TestWslExecutorValidation:
    def setup_method(self):
        self.tool = WslExecutorTool()

    def test_name(self):
        assert self.tool.name == "sys"

    def test_required_parameters(self):
        assert "command" in self.tool.required_parameters

    @pytest.mark.asyncio
    async def test_missing_command_raises(self):
        with pytest.raises(Exception):
            await self.tool.execute()

    @pytest.mark.asyncio
    async def test_workdir_outside_liara_rejected(self):
        result = await self.tool.execute(command="ls", workdir="/etc")
        assert result["status"] == "failed"
        assert "workdir" in result["error"]

    @pytest.mark.asyncio
    async def test_blocked_command_returns_failure(self):
        result = await self.tool.execute(command="rm -rf /home/liara/workspace")
        assert result["status"] == "failed"
        assert "blocked" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_unknown_command_returns_failure(self):
        result = await self.tool.execute(command="evil_binary --flag")
        assert result["status"] == "failed"
        assert "allowed" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_args_must_be_list_of_strings(self):
        result = await self.tool.execute(command="echo", args="hello")
        assert result["status"] == "failed"
        assert "list[str]" in result["error"]

    @pytest.mark.asyncio
    async def test_command_must_be_bare_when_args_used(self):
        result = await self.tool.execute(command="echo hi", args=["there"])
        assert result["status"] == "failed"
        assert "bare executable" in result["error"]

    @pytest.mark.asyncio
    async def test_stdin_text_requires_structured_mode(self):
        result = await self.tool.execute(command="tee /home/liara/workspace/report.txt", stdin_text="x")
        assert result["status"] == "failed"
        assert "structured mode" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_tee_requires_stdin_text(self):
        result = await self.tool.execute(command="tee", args=["/home/liara/workspace/report.txt"])
        assert result["status"] == "failed"
        assert "requires 'stdin_text'" in result["error"]

    @pytest.mark.asyncio
    async def test_stdin_text_restricted_to_enabled_commands(self):
        result = await self.tool.execute(command="cat", args=["/home/liara/workspace/file.txt"], stdin_text="x")
        assert result["status"] == "failed"
        assert "not enabled" in result["error"]

    @pytest.mark.asyncio
    async def test_julia_accepts_stdin_text_in_structured_mode(self):
        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_make_mock_process(b"ok\n", b"", 0))):
            result = await self.tool.execute(
                command="julia",
                args=["--startup-file=no", "/home/liara/workspace/models/demo.jl"],
                stdin_text='{"x":1}',
            )

        assert result["status"] == "success"

    @pytest.mark.asyncio
    async def test_write_commands_require_structured_mode(self):
        result = await self.tool.execute(command="touch /home/liara/workspace/file.txt")
        assert result["status"] == "failed"
        assert "requires structured mode" in result["error"].lower()


# ---------------------------------------------------------------------------
# Execution tests (subprocess mocked)
# ---------------------------------------------------------------------------


def _make_mock_process(stdout: bytes, stderr: bytes, returncode: int):
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    return proc


def _make_verification_process(
    target_path: str,
    *,
    kind: str = "file",
    sha256: str = "a" * 64,
    size_bytes: int = 11,
    verified: bool = True,
):
    import json

    payload = {
        "target_path": target_path,
        "command": "tee" if kind == "file" else "mkdir",
        "write_mode": "overwrite" if kind == "file" else "mkdir",
        "exists": True,
        "kind": kind,
        "size_bytes": size_bytes if kind == "file" else None,
        "sha256": sha256 if kind == "file" else None,
        "content_match": True if kind == "file" else None,
        "verified": verified,
    }
    return _make_mock_process(
        (json.dumps(payload) + "\n").encode("utf-8"),
        b"" if verified else b"verification failed",
        0 if verified else 3,
    )


class TestWslExecutorExecution:
    def setup_method(self):
        self.tool = WslExecutorTool()

    @pytest.mark.asyncio
    async def test_successful_execution(self):
        mock_proc = _make_mock_process(b"hello\n", b"", 0)

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            result = await self.tool.execute(command="python3 -c 'print(\"hello\")'")

        assert result["status"] == "success"
        assert "hello" in result["output"]
        assert result["metadata"]["returncode"] == 0

    @pytest.mark.asyncio
    async def test_nonzero_returncode_returns_failure(self):
        mock_proc = _make_mock_process(b"", b"error msg\n", 1)

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)):
            result = await self.tool.execute(command="python3 -c 'print(1)'")

        assert result["status"] == "failed"
        assert result["metadata"]["returncode"] == 1
        assert "error msg" in result["metadata"]["stderr"]

    @pytest.mark.asyncio
    async def test_timeout_returns_failure(self):
        async def raise_timeout(coro_or_fut, *args, **kwargs):
            # Close any unawaited coroutine to prevent RuntimeWarning.
            if hasattr(coro_or_fut, "close"):
                coro_or_fut.close()
            raise asyncio.TimeoutError()

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("services.tools.builtin.wsl_executor.asyncio.wait_for", side_effect=raise_timeout):
            result = await self.tool.execute(command="python3 -c 'print(1)'", timeout=1)

        assert result["status"] == "failed"
        assert "timed out" in result["error"].lower()
        assert result["metadata"]["transient_error"] is True

    @pytest.mark.asyncio
    async def test_timed_out_overwrite_is_reconciled_by_read_after_write(self):
        write_proc = _make_mock_process(b"", b"", None)
        write_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        write_proc.wait = AsyncMock(return_value=-9)
        verify_proc = _make_verification_process("/home/liara/workspace/pyproject.toml")
        create_mock = AsyncMock(side_effect=[write_proc, verify_proc])

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            result = await self.tool.execute(
                command="tee",
                args=["/home/liara/workspace/pyproject.toml"],
                stdin_text="hello world",
                timeout=1,
            )

        assert result["status"] == "success"
        assert result["metadata"]["returncode"] == 124
        assert result["metadata"]["mutation_verified"] is True
        assert result["metadata"]["mutation_reconciled_after_timeout"] is True
        write_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_threaded_stdin_transport_keeps_verified_write_contract(self):
        completed = MagicMock(returncode=0, stdout=b"artifact\n", stderr=b"")
        verify_proc = _make_verification_process(
            "/home/liara/workspace/.liara_artifacts/report.json",
            sha256="b" * 64,
            size_bytes=9,
        )

        with patch(
            "services.tools.builtin.wsl_executor.subprocess.run",
            return_value=completed,
        ) as threaded_run, patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=verify_proc),
        ):
            result = await self.tool.execute(
                command="tee",
                args=["/home/liara/workspace/.liara_artifacts/report.json"],
                stdin_text="artifact\n",
                stdin_transport="threaded",
                timeout=5,
            )

        assert result["status"] == "success"
        assert result["metadata"]["mutation_verified"] is True
        threaded_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_post_write_wsl_timeout_falls_back_to_host_unc_verification(self):
        write_proc = _make_mock_process(b"written\n", b"", 0)
        verify_proc = _make_mock_process(b"", b"", None)
        verify_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        verify_proc.wait = AsyncMock(return_value=-9)
        create_mock = AsyncMock(side_effect=[write_proc, verify_proc])
        fallback_evidence = {
            "target_path": "/home/liara/workspace/README.md",
            "verified": True,
            "verification": "host_unc_read_after_write_fallback",
        }

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock), \
             patch(
                 "services.tools.builtin.wsl_executor._verify_host_unc_mutation",
                 return_value=fallback_evidence,
             ) as fallback:
            result = await self.tool.execute(
                command="tee",
                args=["/home/liara/workspace/README.md"],
                stdin_text="readme",
                timeout=1,
            )

        assert result["status"] == "success"
        assert result["metadata"]["mutation_verified"] is True
        assert result["metadata"]["mutation_evidence"]["verification"] == "host_unc_read_after_write_fallback"
        fallback.assert_called_once()

    @pytest.mark.asyncio
    async def test_wsl_not_found_returns_failure(self):
        with patch("services.tools.builtin.wsl_executor._resolve_wsl_executable", return_value=None):
            result = await self.tool.execute(command="python3 -c 'print(1)'")

        assert result["status"] == "failed"
        assert "wsl.exe" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_default_workdir_used(self):
        mock_proc = _make_mock_process(b"/home/liara/workspace\n", b"", 0)
        create_mock = AsyncMock(return_value=mock_proc)

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            await self.tool.execute(command="python3 -c 'print(1)'")

        captured_args = list(create_mock.call_args.args)
        assert "--cd" in captured_args
        idx = captured_args.index("--cd")
        assert captured_args[idx + 1] == "/home/liara/workspace"

    @pytest.mark.asyncio
    async def test_custom_workdir_within_liara(self):
        mock_proc = _make_mock_process(b"ok\n", b"", 0)
        create_mock = AsyncMock(return_value=mock_proc)

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            await self.tool.execute(command="python3 -c 'print(1)'", workdir="/home/liara/tmp")

        captured_args = list(create_mock.call_args.args)
        idx = captured_args.index("--cd")
        assert captured_args[idx + 1] == "/home/liara/tmp"

    @pytest.mark.asyncio
    async def test_structured_command_exec_mode(self):
        mock_proc = _make_mock_process(b"ok\n", b"", 0)
        create_mock = AsyncMock(return_value=mock_proc)

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            result = await self.tool.execute(command="python3", args=["-c", "print('hello')"])

        captured_args = list(create_mock.call_args.args)
        assert result["status"] == "success"
        assert "sh" not in captured_args
        assert "python3" in captured_args
        assert "print('hello')" in captured_args

    @pytest.mark.asyncio
    async def test_venv_pip_resolves_to_workspace_environment(self):
        mock_proc = _make_mock_process(b"installed\n", b"", 0)
        create_mock = AsyncMock(return_value=mock_proc)

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            result = await self.tool.execute(
                command="venv-pip",
                args=["install", "--disable-pip-version-check", "--no-input", "pytest"],
            )

        captured_args = list(create_mock.call_args.args)
        assert result["status"] == "success"
        assert "/home/liara/workspace/.venv/bin/pip" in captured_args
        assert "--exec" in captured_args

    @pytest.mark.asyncio
    async def test_venv_pip_bootstraps_missing_workspace_environment_and_preserves_specifier(self):
        probe_missing = _make_mock_process(b"", b"", 1)
        bootstrap_ok = _make_mock_process(b"", b"", 0)
        install_ok = _make_mock_process(b"installed\n", b"", 0)
        create_mock = AsyncMock(side_effect=[probe_missing, bootstrap_ok, install_ok])

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            result = await self.tool.execute(
                command="venv-pip",
                args=["install", "--disable-pip-version-check", "--no-input", "pydantic>=2.0", "pytest"],
            )

        bootstrap_args = list(create_mock.call_args_list[1].args)
        install_args = list(create_mock.call_args_list[2].args)
        assert bootstrap_args[-4:] == ["python3", "-m", "venv", ".venv"]
        assert "--exec" in install_args
        assert "pydantic>=2.0" in install_args
        assert result["status"] == "success"
        assert result["metadata"]["venv_bootstrap"]["venv_bootstrapped"] is True

    @pytest.mark.asyncio
    async def test_health_alias_executes_direct_health_probe(self):
        class _FakeHealthResponse:
            status_code = 200
            text = '{"status":"ok"}'

            def raise_for_status(self):
                return None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_FakeHealthResponse())

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_client
        mock_ctx.__aexit__.return_value = False

        with patch("services.tools.builtin.wsl_executor.httpx.AsyncClient", return_value=mock_ctx):
            result = await self.tool.execute(command="health")

        assert result["status"] == "success"
        assert "status" in result["output"]
        assert result["metadata"]["command"].endswith("/health")

    @pytest.mark.asyncio
    async def test_health_alias_returns_backend_and_heartbeat_snapshot(self):
        class _FakeHealthResponse:
            def __init__(self, payload):
                self.status_code = 200
                self._payload = payload
                self.text = json.dumps(payload)

            def json(self):
                return self._payload

            def raise_for_status(self):
                return None

        responses = [
            _FakeHealthResponse({"status": "ok", "service": "liara-api"}),
            _FakeHealthResponse(
                {
                    "backend_health": {"postgres": "healthy", "embedding": "healthy"},
                    "device": "NPU",
                    "execution_devices": ["NPU"],
                    "runtime_backend": "openvino-cpp",
                    "model": "qwen",
                    "dimensions": 1024,
                }
            ),
            _FakeHealthResponse(
                {
                    "status": "success",
                    "service_health": {"state": "healthy"},
                    "curve": {
                        "state": "healthy",
                        "trend": "stable",
                        "stability": 0.9,
                        "confidence": 0.95,
                        "envelope": {"capacity": 0.42},
                    },
                }
            ),
        ]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=responses)

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_client
        mock_ctx.__aexit__.return_value = False

        with patch("services.tools.builtin.wsl_executor.httpx.AsyncClient", return_value=mock_ctx):
            result = await self.tool.execute(command="health")

        payload = json.loads(result["output"])
        assert result["status"] == "success"
        assert payload["backend_health"]["embedding"] == "healthy"
        assert payload["embedding_runtime"]["device"] == "NPU"
        assert payload["embedding_runtime"]["runtime_backend"] == "openvino-cpp"
        assert payload["heartbeat"]["state"] == "healthy"
        assert payload["heartbeat"]["envelope"]["capacity"] == 0.42
        assert mock_client.get.await_count == 3

    @pytest.mark.asyncio
    async def test_legacy_sys_health_string_executes_direct_health_probe(self):
        class _FakeHealthResponse:
            status_code = 200
            text = '{"status":"ok"}'

            def raise_for_status(self):
                return None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_FakeHealthResponse())

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_client
        mock_ctx.__aexit__.return_value = False

        with patch("services.tools.builtin.wsl_executor.httpx.AsyncClient", return_value=mock_ctx):
            result = await self.tool.execute(command="sys health")

        assert result["status"] == "success"
        assert result["metadata"]["command"].endswith("/health")

    @pytest.mark.asyncio
    async def test_legacy_sys_health_structured_executes_direct_health_probe(self):
        class _FakeHealthResponse:
            status_code = 200
            text = '{"status":"ok"}'

            def raise_for_status(self):
                return None

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_FakeHealthResponse())

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__.return_value = mock_client
        mock_ctx.__aexit__.return_value = False

        with patch("services.tools.builtin.wsl_executor.httpx.AsyncClient", return_value=mock_ctx):
            result = await self.tool.execute(command="sys", args=["health"])

        assert result["status"] == "success"
        assert result["metadata"]["command"].endswith("/health")

    @pytest.mark.asyncio
    async def test_structured_tee_forwards_stdin_bytes(self):
        mock_proc = _make_mock_process(b"written\n", b"", 0)
        verify_proc = _make_verification_process("/home/liara/workspace/report.txt")
        communicated = {}
        create_mock = AsyncMock(side_effect=[mock_proc, verify_proc])

        async def _communicate(*, input=None):
            communicated["input"] = input
            return (b"written\n", b"")

        mock_proc.communicate = AsyncMock(side_effect=_communicate)

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            result = await self.tool.execute(
                command="tee",
                args=["/home/liara/workspace/report.txt"],
                stdin_text="hello world",
            )

        captured_kwargs = dict(create_mock.call_args_list[0].kwargs)
        assert result["status"] == "success"
        assert captured_kwargs["stdin"] == asyncio.subprocess.PIPE
        assert communicated["input"] == b"hello world"
        assert result["metadata"]["stdin_bytes"] == 11
        assert result["metadata"]["target_path"] == "/home/liara/workspace/report.txt"
        assert result["metadata"]["mutation_verified"] is True
        assert result["metadata"]["mutation_evidence"]["verification"] == "wsl_read_after_write"

    @pytest.mark.asyncio
    async def test_structured_temp_append_tee_reports_scope(self):
        mock_proc = _make_mock_process(b"written\n", b"", 0)
        verify_proc = _make_verification_process("/home/liara/temp/report.txt")
        create_mock = AsyncMock(side_effect=[mock_proc, verify_proc])

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            result = await self.tool.execute(
                command="tee",
                args=["-a", "/home/liara/temp/report.txt"],
                stdin_text="hello world",
            )

        assert result["status"] == "success"
        assert result["metadata"]["storage_scope"] == "temp"
        assert result["metadata"]["retention_hint_seconds"] == 86400
        assert result["metadata"]["write_mode"] == "append"
        assert result["metadata"]["mutation_verified"] is True

    @pytest.mark.asyncio
    async def test_write_fails_when_read_after_write_cannot_verify_target(self):
        write_proc = _make_mock_process(b"written\n", b"", 0)
        verify_proc = _make_verification_process(
            "/home/liara/workspace/missing.txt",
            verified=False,
        )
        create_mock = AsyncMock(side_effect=[write_proc, verify_proc])

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            result = await self.tool.execute(
                command="tee",
                args=["/home/liara/workspace/missing.txt"],
                stdin_text="hello world",
            )

        assert result["status"] == "failed"
        assert result["metadata"]["returncode"] == 0
        assert result["metadata"]["mutation_verified"] is False
        assert "post-write verification failed" in result["error"]

    @pytest.mark.asyncio
    async def test_mkdir_verification_uses_non_flag_target(self):
        write_proc = _make_mock_process(b"", b"", 0)
        verify_proc = _make_verification_process(
            "/home/liara/workspace/demo",
            kind="directory",
        )
        create_mock = AsyncMock(side_effect=[write_proc, verify_proc])

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            result = await self.tool.execute(
                command="mkdir",
                args=["-p", "/home/liara/workspace/demo"],
            )

        assert result["status"] == "success"
        assert result["metadata"]["target_path"] == "/home/liara/workspace/demo"
        assert result["metadata"]["mutation_verified"] is True

    @pytest.mark.asyncio
    async def test_julia_uses_env_override_path_with_telemetry(self, monkeypatch):
        monkeypatch.setenv("LIARA_WSL_JULIA_PATH", "/opt/custom/julia")
        mock_proc = _make_mock_process(b"ok\n", b"", 0)
        create_mock = AsyncMock(return_value=mock_proc)

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            result = await self.tool.execute(command="julia", args=["--version"])

        assert result["status"] == "success"
        captured_args = list(create_mock.call_args.args)
        assert "/opt/custom/julia" in captured_args
        assert result["metadata"]["julia_resolution"]["strategy"] == "env_override"
        assert result["metadata"]["julia_resolution"]["fallback_used"] is False

    @pytest.mark.asyncio
    async def test_julia_uses_command_v_discovery_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("LIARA_WSL_JULIA_PATH", raising=False)
        discover_proc = _make_mock_process(b"/usr/bin/julia\n", b"", 0)
        run_proc = _make_mock_process(b"ok\n", b"", 0)
        create_mock = AsyncMock(side_effect=[discover_proc, run_proc])

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            result = await self.tool.execute(command="julia", args=["--version"])

        assert result["status"] == "success"
        run_args = list(create_mock.call_args_list[1].args)
        assert "/usr/bin/julia" in run_args
        assert result["metadata"]["julia_resolution"]["strategy"] == "wsl_command_v"
        assert result["metadata"]["julia_resolution"]["fallback_used"] is True

    @pytest.mark.asyncio
    async def test_julia_falls_back_to_juliaup_path_when_discovery_fails(self, monkeypatch):
        monkeypatch.delenv("LIARA_WSL_JULIA_PATH", raising=False)
        discover_proc = _make_mock_process(b"", b"not found\n", 1)
        run_proc = _make_mock_process(b"ok\n", b"", 0)
        create_mock = AsyncMock(side_effect=[discover_proc, run_proc])

        with patch("shutil.which", return_value="/usr/bin/wsl"), \
             patch("asyncio.create_subprocess_exec", new=create_mock):
            result = await self.tool.execute(command="julia", args=["--version"])

        assert result["status"] == "success"
        run_args = list(create_mock.call_args_list[1].args)
        assert "/home/liara/.juliaup/bin/julia" in run_args
        assert result["metadata"]["julia_resolution"]["strategy"] == "default_juliaup"
        assert result["metadata"]["julia_resolution"]["fallback_used"] is True
