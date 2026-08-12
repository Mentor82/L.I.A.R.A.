from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest

from services.simulation.wsl_session_runtime import (
    WslSessionConfig,
    WslSessionError,
    WslSessionManager,
)
from services.tools.builtin.wsl_executor import WslExecutorTool
from services.tools.builtin.wsl_session import WslSessionTool
from services.tools.registry import get_tool_registry


class FakeWslRunner:
    def __init__(self, bridge_root: Path):
        self.bridge_root = bridge_root
        self.calls: list[dict] = []

    def bridge(self, wsl_path: str) -> Path:
        return self.bridge_root.joinpath(*Path(wsl_path.lstrip("/")).parts)

    def run(
        self,
        argv,
        *,
        user,
        cwd=None,
        input_bytes=None,
        timeout=60,
        allowed_returncodes=frozenset({0}),
    ):
        del input_bytes, timeout, allowed_returncodes
        self.calls.append({"argv": list(argv), "user": user, "cwd": cwd})
        if argv[:2] == ["rm", "-rf"]:
            shutil.rmtree(self.bridge(argv[-1]), ignore_errors=False)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv and argv[0] == "diff":
            source = self.bridge(f"{cwd}/source/app.py").read_text(encoding="utf-8")
            work = self.bridge(f"{cwd}/work/app.py").read_text(encoding="utf-8")
            if source == work:
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            patch_bytes = (
                "--- source/app.py\n"
                "+++ work/app.py\n"
                f"-print({source.split('print(', 1)[1]}"
                f"+print({work.split('print(', 1)[1]}"
            ).encode("utf-8")
            return subprocess.CompletedProcess(argv, 1, patch_bytes, b"")
        return subprocess.CompletedProcess(argv, 0, b"", b"")


@pytest.fixture
def session_manager(tmp_path: Path) -> tuple[WslSessionManager, FakeWslRunner, Path]:
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (canonical / "app.py").write_text("print('canonical')\n", encoding="utf-8")
    (canonical / ".env").write_text("SECRET=never-copy", encoding="utf-8")
    (canonical / ".env.example").write_text("SECRET=example", encoding="utf-8")
    (canonical / "__pycache__").mkdir()
    (canonical / "__pycache__" / "app.pyc").write_bytes(b"cache")

    bridge_root = tmp_path / "bridge"
    bridge_root.mkdir()
    config = WslSessionConfig(
        canonical_root=canonical,
        local_artifacts_root=tmp_path / "artifacts",
        local_registry_root=tmp_path / "registry",
        audit_path=tmp_path / "audit.jsonl",
        bridge_root=bridge_root,
        max_snapshot_bytes=1024 * 1024,
    )
    runner = FakeWslRunner(bridge_root)
    return WslSessionManager(config, runner), runner, canonical


def test_session_lifecycle_keeps_canonical_tree_immutable(session_manager) -> None:
    manager, runner, canonical = session_manager
    created = manager.create(
        label="unit-simulation",
        request_id="req-1",
        run_id="run-1",
        trace_session_id="trace-session-1",
        source="pytest",
    )

    source = manager._bridge_path(created["source_path"])
    work = manager._bridge_path(created["work_path"])
    assert (source / "app.py").read_text(encoding="utf-8") == "print('canonical')\n"
    assert not (source / ".env").exists()
    assert (source / ".env.example").is_file()
    assert not (source / "__pycache__").exists()

    (work / "app.py").write_text("print('candidate')\n", encoding="utf-8")
    collection = manager.collect(created["session_id"])

    assert collection["changed"] is True
    assert Path(collection["candidate_workspace"], "app.py").read_text(encoding="utf-8") == "print('candidate')\n"
    assert Path(collection["patch_path"]).is_file()
    assert collection["validator_request"]["metadata"]["snapshot_hash"] == created["snapshot_hash"]
    assert (canonical / "app.py").read_text(encoding="utf-8") == "print('canonical')\n"

    destroyed = manager.destroy(created["session_id"])
    assert destroyed["state"] == "destroyed"
    assert not manager._bridge_path(created["wsl_root"]).exists()
    cleanup_calls = [call["argv"] for call in runner.calls if call["argv"][0] in {"chmod", "rm"}]
    assert cleanup_calls[-2] == ["chmod", "-R", "u+w", created["wsl_root"]]
    assert cleanup_calls[-1] == ["rm", "-rf", "--", created["wsl_root"]]


def test_execution_context_is_bound_to_registered_work_tree(session_manager) -> None:
    manager, _, _ = session_manager
    created = manager.create()
    context = manager.execution_context(created["session_id"])
    assert context["workdir"] == created["work_path"]
    assert context["execution_user"] == "liara"
    with pytest.raises(WslSessionError):
        manager.execution_context("sess-../../escape")


def test_snapshot_limit_fails_closed_and_removes_partial_session(session_manager) -> None:
    manager, _, canonical = session_manager
    manager.config = WslSessionConfig(
        canonical_root=canonical,
        local_artifacts_root=manager.config.local_artifacts_root,
        local_registry_root=manager.config.local_registry_root,
        audit_path=manager.config.audit_path,
        bridge_root=manager.config.bridge_root,
        max_snapshot_bytes=2,
    )
    with pytest.raises(WslSessionError, match="snapshot exceeds"):
        manager.create()
    session_roots = list((manager.config.bridge_root / "home" / "liara" / "workspace" / "sessions").glob("sess-*"))
    assert session_roots == []


@pytest.mark.asyncio
async def test_sys_rejects_workdir_outside_selected_session() -> None:
    context = {
        "session_id": "sess-0123456789abcdef",
        "execution_user": "liara",
        "workdir": "/home/liara/workspace/sessions/sess-0123456789abcdef/work",
        "snapshot_hash": "a" * 64,
    }
    with patch(
        "services.tools.builtin.wsl_executor.WslSessionManager.execution_context",
        return_value=context,
    ):
        result = await WslExecutorTool().execute(
            command="ls",
            args=[],
            workspace_session_id="sess-0123456789abcdef",
            workdir="/home/liara/workspace/other",
        )
    assert result["status"] == "failed"
    assert "outside the selected WSL session" in result["error"]


def test_tool_registry_exposes_session_lifecycle() -> None:
    assert "wsl_session" in get_tool_registry().list_tools()


def test_wsl_session_tool_accepts_api_trace_context() -> None:
    tool = WslSessionTool()
    tool._validate_parameters(action="plan", context="api.tools.wsl_session.invoke")
    assert "context" in tool.optional_parameters
