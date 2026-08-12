#!/usr/bin/env python3
"""Quick test to verify validator workspace artifact persistence."""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime

# Test artifact persistence module directly
def test_artifact_persistence_module():
    """Test artifact persistence functions work in isolation."""
    from services.workspace import (
        persist_validation_report,
        persist_governance_decision,
        list_workspace_artifacts,
        get_workspace_status,
    )
    
    print("✓ Artifact persistence module imports OK")
    
    # Create a temp workspace for testing
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["LIARA_WORKSPACE_PATH"] = tmpdir
        
        # Test validation report persistence
        report_path = persist_validation_report(
            job_id="test-validator-001",
            scope="quick",
            findings=[
                {"severity": "warning", "message": "Test finding"}
            ],
            exit_code=0,
            execution_mode="mock",
            session_id="test-session-1",
        )
        assert report_path.exists(), "Report file not created"
        print(f"✓ Validation report persisted: {report_path.name}")
        
        # Test governance decision persistence
        decision_path = persist_governance_decision(
            governance_id="gov-test-001",
            command="memory-consolidate",
            risk_tokens=["memory:write"],
            decision_approved=True,
            approver="system",
            reason="Auto-approved in test mode",
            session_id="test-session-1",
        )
        assert decision_path.exists(), "Decision file not created"
        print(f"✓ Governance decision persisted: {decision_path.name}")
        
        # Test listing artifacts
        artifacts = list_workspace_artifacts(artifact_type="validation", limit=10)
        assert len(artifacts) > 0, "No validation artifacts listed"
        print(f"✓ Listed {len(artifacts)} validation artifacts")
        
        # Test workspace status
        status = get_workspace_status()
        assert status["validation_count"] > 0, "Validation count not updated"
        print(f"✓ Workspace status: {json.dumps(status, indent=2)}")
        
    print("\n✅ All artifact persistence tests passed!")


async def test_validator_job_with_persistence():
    """Test validator job integration with persistence."""
    from services.memory.store import _execute_validator_job
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["LIARA_WORKSPACE_PATH"] = tmpdir
        os.environ["LIARA_VALIDATOR_EXECUTION_MODE"] = "mock"
        
        # Run mock validator job
        result = await asyncio.to_thread(
            _execute_validator_job,
            job_id="test-job-001",
            workspace=tmpdir,
            scope="quick",
            checks=[],
            strict_mode=False,
            session_id="test-session-2",
        )
        
        assert result["state"] == "completed", f"Validator failed: {result}"
        print(f"✓ Mock validator completed: {result['summary']}")
        
        # Check that artifact was persisted
        from services.workspace import list_workspace_artifacts
        artifacts = list_workspace_artifacts(artifact_type="validation", limit=10)
        assert len(artifacts) > 0, "Validator report not persisted"
        print(f"✓ Validator artifact persisted: {artifacts[0]['filename']}")
        
    print("\n✅ Validator job persistence test passed!")


if __name__ == "__main__":
    print("=" * 70)
    print("LIARA Workspace Artifact Persistence Integration Tests")
    print("=" * 70)
    
    # Run tests
    test_artifact_persistence_module()
    asyncio.run(test_validator_job_with_persistence())
    
    print("\n" + "=" * 70)
    print("✅ ALL TESTS PASSED - Workspace artifacts integration working!")
    print("=" * 70)
