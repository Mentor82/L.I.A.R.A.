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
