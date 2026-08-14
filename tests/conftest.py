"""Pytest bootstrap for repository-local imports.

Ensures `src` package imports resolve no matter from which working directory
pytest is executed.
"""

from pathlib import Path
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root_str = str(PROJECT_ROOT)
if project_root_str not in sys.path:
    sys.path.insert(0, project_root_str)


@pytest.fixture(autouse=True)
def isolate_workspace_artifacts(tmp_path, monkeypatch):
    """Keep tests isolated from runtime artifacts and managed services."""

    from services.config import Settings
    from services.workspace import artifact_persistence

    monkeypatch.setenv("LIARA_ARTIFACT_STORE_MODE", "local")
    monkeypatch.setattr(Settings, "LLAMA_CPP_MANAGED_BY_API", False)
    monkeypatch.setattr(
        artifact_persistence,
        "_WORKSPACE_ROOT",
        tmp_path / "liara-test-workspace",
    )


@pytest.fixture(autouse=True)
def isolate_sys_audit_repository():
    """Reset sys_audit's module-global Postgres repository around every test.

    services.tools.builtin.sys_audit.configure_sys_audit_repository() is
    called by create_api_app() and sets a module-level global -- without
    resetting it, any test that builds an app (even with pool=None) leaves
    later tests' log_blocked/log_executed/log_judge_pre_action calls routed
    to that repository instead of the JSONL fallback those tests assert on.

    Deliberately not monkeypatch.setattr: its teardown restores whatever
    value was present *before this fixture's own setup* ran -- which, once
    some earlier test's body has called create_api_app() again, is already a
    stale non-None repository, not None. Explicit reset on both sides of the
    test avoids that propagation entirely.
    """
    import services.tools.builtin.sys_audit as sys_audit_module

    sys_audit_module._repo = None
    yield
    sys_audit_module._repo = None
