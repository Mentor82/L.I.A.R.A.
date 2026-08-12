#!/usr/bin/env python3
"""Integration test verifying workspace artifact persistence end-to-end."""

import asyncio
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path


async def test_end_to_end_integration():
    """Test complete integration: Validator -> Governance -> Memory."""
    
    print("=" * 80)
    print("LIARA Workspace Artifact Persistence - End-to-End Integration Test")
    print("=" * 80)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["LIARA_WORKSPACE_PATH"] = tmpdir
        os.environ["LIARA_VALIDATOR_EXECUTION_MODE"] = "mock"
        
        # Setup
        from services.workspace import (
            persist_validation_report,
            persist_governance_decision,
            persist_memory_consolidation,
            persist_chat_output,
            list_workspace_artifacts,
            get_workspace_status,
        )
        from services.memory.store import _execute_validator_job
        from services.contracts import MemoryDreamingRunRequest
        
        workspace = Path(tmpdir)
        artifacts_dir = workspace / ".liara_artifacts"
        
        print("\n1️⃣  VALIDATOR INTEGRATION TEST")
        print("-" * 80)
        
        # Test 1: Validator job with persistence
        job_id = "e2e-validator-001"
        result = await asyncio.to_thread(
            _execute_validator_job,
            job_id=job_id,
            workspace=str(tmpdir),
            scope="quick",
            checks=[],
            strict_mode=False,
            session_id="session-e2e-001",
        )
        print(f"✓ Mock validator completed: {result['state']}")
        
        # Check validation report was persisted
        validation_reports = artifacts_dir / "validation-reports"
        assert validation_reports.exists(), "Validation reports dir not created"
        val_files = list(validation_reports.glob("*.json"))
        assert len(val_files) > 0, "No validation reports persisted"
        print(f"✓ Validation report persisted: {val_files[0].name}")
        
        with open(val_files[0]) as f:
            val_data = json.load(f)
            assert val_data["job_id"] == job_id, "Job ID mismatch"
            assert val_data["session_id"] == "session-e2e-001", "Session ID mismatch"
        print(f"✓ Report metadata validated")
        
        print("\n2️⃣  GOVERNANCE INTEGRATION TEST")
        print("-" * 80)
        
        # Test 2: Governance decision persistence
        gov_id = "e2e-governance-001"
        gov_path = persist_governance_decision(
            governance_id=gov_id,
            command="memory-consolidate",
            risk_tokens=["memory:write", "session:modify"],
            decision_approved=True,
            approver="test_agent",
            reason="Approved for E2E testing",
            session_id="session-e2e-001",
        )
        print(f"✓ Governance decision persisted: {gov_path.name}")
        
        with open(gov_path) as f:
            gov_data = json.load(f)
            assert gov_data["governance_id"] == gov_id, "Governance ID mismatch"
            assert gov_data["decision_approved"] == True, "Decision not approved"
        print(f"✓ Governance metadata validated")
        
        print("\n3️⃣  MEMORY CONSOLIDATION INTEGRATION TEST")
        print("-" * 80)
        
        # Test 3: Memory consolidation persistence
        run_id = "e2e-dreaming-001"
        proposals = [
            {
                "proposal_id": "prop-001",
                "content": "Test consolidation proposal 1",
                "proposed_status": "candidate",
            },
            {
                "proposal_id": "prop-002",
                "content": "Test consolidation proposal 2",
                "proposed_status": "candidate",
            },
        ]
        con_path = persist_memory_consolidation(
            dreaming_run_id=run_id,
            proposals=proposals,
            verified_facts=[],
            session_id="session-e2e-001",
        )
        print(f"✓ Memory consolidation persisted: {con_path.name}")
        
        with open(con_path) as f:
            con_data = json.load(f)
            assert con_data["dreaming_run_id"] == run_id, "Run ID mismatch"
            assert len(con_data["proposals"]) == 2, "Proposal count mismatch"
        print(f"✓ Consolidation metadata validated")
        
        print("\n4️⃣  CHAT OUTPUT INTEGRATION TEST")
        print("-" * 80)
        
        # Test 4: Chat output persistence
        chat_path = persist_chat_output(
            output_type="generated_code",
            content="def hello():\n    print('Hello from LIARA')",
            metadata={"language": "python", "framework": "none"},
            session_id="session-e2e-001",
        )
        print(f"✓ Chat output persisted: {chat_path.name}")
        
        with open(chat_path) as f:
            chat_data = json.load(f)
            assert chat_data["output_type"] == "generated_code", "Output type mismatch"
            assert "hello" in chat_data["content"], "Content not persisted"
        print(f"✓ Chat output metadata validated")
        
        print("\n5️⃣  ARTIFACT QUERY & STATUS TEST")
        print("-" * 80)
        
        # Test 5: Querying artifacts
        validation_list = list_workspace_artifacts(artifact_type="validation", limit=10)
        assert len(validation_list) > 0, "No validation artifacts found"
        print(f"✓ Query validation artifacts: {len(validation_list)} found")
        
        governance_list = list_workspace_artifacts(artifact_type="governance", limit=10)
        assert len(governance_list) > 0, "No governance artifacts found"
        print(f"✓ Query governance artifacts: {len(governance_list)} found")
        
        consolidation_list = list_workspace_artifacts(artifact_type="consolidation", limit=10)
        assert len(consolidation_list) > 0, "No consolidation artifacts found"
        print(f"✓ Query consolidation artifacts: {len(consolidation_list)} found")
        
        chat_list = list_workspace_artifacts(artifact_type="chat", limit=10)
        assert len(chat_list) > 0, "No chat artifacts found"
        print(f"✓ Query chat artifacts: {len(chat_list)} found")
        
        # Test 6: Workspace status
        status = get_workspace_status()
        print(f"✓ Workspace status retrieved:")
        for key, value in status.items():
            if not key.startswith("_"):
                print(f"   • {key}: {value}")
        
        print("\n" + "=" * 80)
        print("✅ ALL END-TO-END INTEGRATION TESTS PASSED!")
        print("=" * 80)
        print("\nSummary:")
        print(f"  • Workspace: {tmpdir}")
        print(f"  • Validation reports: {len(val_files)}")
        print(f"  • Governance decisions: {len(list(artifacts_dir / 'governance-decisions' / '*.json'))}")
        print(f"  • Memory consolidations: {len(list(artifacts_dir / 'memory-consolidations' / '*.json'))}")
        print(f"  • Chat outputs: {len(list(artifacts_dir / 'chat-outputs' / '*.json'))}")
        print()


if __name__ == "__main__":
    asyncio.run(test_end_to_end_integration())
