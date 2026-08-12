"""Tests for timezone-aware workspace artifact persistence."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

import pytest

from services.workspace import artifact_persistence as persistence


@pytest.fixture
def artifact_dirs(tmp_path, monkeypatch):
    artifacts = tmp_path / ".liara_artifacts"
    monkeypatch.setenv("LIARA_ARTIFACT_STORE_MODE", "local")
    monkeypatch.setattr(persistence, "_WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(persistence, "_ARTIFACTS_DIR", artifacts)
    monkeypatch.setattr(persistence, "_VALIDATION_REPORTS_DIR", artifacts / "validation-reports")
    monkeypatch.setattr(persistence, "_GOVERNANCE_DIR", artifacts / "governance-decisions")
    monkeypatch.setattr(persistence, "_MEMORY_DIR", artifacts / "memory-consolidations")
    monkeypatch.setattr(persistence, "_CHAT_OUTPUTS_DIR", artifacts / "chat-outputs")
    return artifacts


def _assert_aware_utc(timestamp: str) -> None:
    parsed = datetime.fromisoformat(timestamp)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


def test_all_artifact_types_use_aware_utc_and_compact_filename_timestamps(artifact_dirs):
    validation = persistence.persist_validation_report(
        job_id="123456789",
        scope="quick",
        findings=[],
        exit_code=0,
        execution_mode="worker",
        session_id="session-a",
    )
    governance = persistence.persist_governance_decision(
        governance_id="abcdefghijk",
        command="health",
        risk_tokens=[],
        decision_approved=True,
        approver="human",
        reason="test",
        session_id="session-a",
    )
    consolidation = persistence.persist_memory_consolidation(
        dreaming_run_id="dream123456",
        proposals=[],
        verified_facts=[],
        session_id="session-a",
    )
    chat_output = persistence.persist_chat_output(
        output_type="code",
        content="print('ok')\n",
        metadata={"kind": "python"},
        session_id="session-a",
    )

    assert re.fullmatch(r"validation-12345678-quick-\d{8}T\d{6}Z\.json", validation.name)
    assert re.fullmatch(r"governance-abcdefgh-approved-\d{8}T\d{6}Z\.json", governance.name)
    assert re.fullmatch(r"consolidation-dream123-\d{8}T\d{6}Z\.json", consolidation.name)
    assert re.fullmatch(r"output-code-\d{8}T\d{6}Z\.json", chat_output.name)

    for path in (validation, governance, consolidation):
        payload = json.loads(path.read_text(encoding="utf-8"))
        _assert_aware_utc(payload["timestamp"])

    chat_payload = json.loads(chat_output.read_text(encoding="utf-8"))
    _assert_aware_utc(chat_payload["timestamp"])
    assert chat_payload["content"] == "print('ok')\n"
    assert chat_payload["metadata"] == {"kind": "python"}

    for path in (validation, governance, consolidation, chat_output):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["traceability"]["request_id"]
        assert payload["traceability"]["run_id"]


def test_read_helpers_do_not_create_workspace_directories(artifact_dirs):
    assert not artifact_dirs.exists()

    status = persistence.get_workspace_status()
    artifacts = persistence.list_workspace_artifacts(limit=5)

    assert status["exists"] is True
    assert status["artifact_counts"] == {
        "validation": 0,
        "governance": 0,
        "memory": 0,
        "chat": 0,
    }
    assert artifacts == []
    assert not artifact_dirs.exists()


def test_wsl_store_uses_verified_sys_mutations(monkeypatch, tmp_path):
    from services.tools.builtin import wsl_executor
    from services.workspace.artifact_store import ArtifactStore

    calls = []

    class FakeWslExecutorTool:
        async def execute(self, **kwargs):
            calls.append(kwargs)
            return {
                "status": "success",
                "metadata": {
                    "mutation_verified": True,
                    "mutation_evidence": {
                        "verified": True,
                        "sha256": "a" * 64,
                    },
                },
            }

    monkeypatch.setattr(wsl_executor, "WslExecutorTool", FakeWslExecutorTool)
    store = ArtifactStore(
        mode="wsl",
        canonical_root="/home/liara/workspace",
        local_root=tmp_path,
        distro="Debian",
        user="liara",
    )

    path = store.write_json(
        artifact_dir="validation-reports",
        filename="report.json",
        payload={"status": "ok"},
        request_id="request-a",
        run_id="run-a",
        session_id="session-a",
        source="unit-test",
    )

    assert str(path) == "/home/liara/workspace/.liara_artifacts/validation-reports/report.json"
    assert [call["command"] for call in calls] == ["mkdir", "tee"]
    assert calls[1]["stdin_text"].endswith("\n")
    assert calls[1]["request_id"] == "request-a"
    assert calls[1]["run_id"] == "run-a"
    assert calls[1]["source"] == "unit-test"
