#!/usr/bin/env python3
"""Direct test of date command via WslExecutorTool."""
import asyncio
import sys
sys.path.insert(0, '.')
from services.tools.builtin.wsl_executor import WslExecutorTool

async def test():
    tool = WslExecutorTool()
    result = await tool.execute(command='date', args=['+%Y-%m-%d %H:%M:%S UTC'])
    print(f'Status: {result["status"]}')
    if result['status'] == 'failed':
        print(f'Error: {result["error"]}')
    else:
        print(f'Output: {result.get("output", "N/A")}')
        print(f'Output length: {len(result.get("output", ""))}')

asyncio.run(test())
