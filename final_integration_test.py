#!/usr/bin/env python3
"""Final integration test: date/time feature end-to-end."""
import asyncio
import sys
sys.path.insert(0, '.')

from services.orchestrator.sys_selector import needs_sys, select_sys_command
from services.tools.builtin.wsl_executor import WslExecutorTool
from services.tools.builtin.sys_command_policy import check_command_request

async def test_date_time_pipeline():
    """Test complete date/time command pipeline."""
    
    # Step 1: Query detection
    query = "Nenne mir die aktuelle Zeit bitte"
    assert needs_sys(query), "Query should need sys tool"
    print(f"✓ Step 1: Query detected as time query: '{query}'")
    
    # Step 2: Command selection
    selection = select_sys_command(query)
    assert selection.command == "date", f"Expected 'date' command, got '{selection.command}'"
    assert selection.intent == "datetime", f"Expected 'datetime' intent, got '{selection.intent}'"
    print(f"✓ Step 2: Command selected: {selection.command} with args {selection.args}")
    
    # Step 3: Policy validation
    policy_result = check_command_request(selection.command, selection.args)
    assert policy_result.allowed, f"Command should be allowed, but got error: {policy_result.error}"
    print(f"✓ Step 3: Policy check passed (allowed={policy_result.allowed})")
    
    # Step 4: Execution
    tool = WslExecutorTool()
    result = await tool.execute(command=selection.command, args=selection.args)
    assert result['status'] == 'success', f"Execution failed: {result.get('error')}"
    assert '20' in result['output'], f"Output should contain year, got: {result['output']}"
    print(f"✓ Step 4: Command executed successfully")
    print(f"  Output: {result['output']}")
    
    return result['output']

# Run the test
print("=" * 70)
print("DATE/TIME COMMAND PIPELINE INTEGRATION TEST")
print("=" * 70)

output = asyncio.run(test_date_time_pipeline())

print("=" * 70)
print(f"✓✓✓ COMPLETE SUCCESS ✓✓✓")
print(f"Date/time commands are fully operational.")
print(f"Current time from sys tool: {output}")
print("=" * 70)
