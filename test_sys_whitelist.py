#!/usr/bin/env python3
"""Test whitelisted sys commands via /tools/sys/invoke."""

import json
import httpx
import asyncio

async def test_sys_tool(command: str, args: list[str] | None = None):
    """Test a sys command via /tools/sys/invoke."""
    
    payload = {
        "parameters": {
            "command": command,
            **({"args": args} if args else {})
        },
        "timeout_seconds": 10
    }
    
    print(f"\n{'='*70}")
    print(f"Testing: {command} {' '.join(args or [])}")
    print(f"{'='*70}")
    
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(
                "http://127.0.0.1:8010/tools/sys/invoke",
                json=payload,
            )
            
            data = response.json()
            
            print(f"Status: {data['status']}")
            if data.get('error'):
                print(f"Error: {data['error'][:200]}")
            if data.get('output'):
                output_str = str(data['output'])[:300]
                print(f"Output:\n{output_str}")
            print(f"Execution: {data.get('execution_ms', 'N/A')} ms")
            
            return data['status'] == 'success'
            
        except Exception as e:
            print(f"✗ Request failed: {e}")
            return False

async def main():
    """Test suite of whitelisted commands."""
    
    tests = [
        # ls - listing
        ("ls", ["/home/liara/workspace"]),
        
        # python3 - simple script
        ("python3", ["-c", "print('Hello from Python')"]),
        
        # date/time
        ("date", None),
        ("time", ["echo", "test"]),
        
        # grep - text search
        ("grep", ["-r", "test", "/home/liara/workspace"]),
        
        # tee - structured write
        ("tee", ["/tmp/test.txt"]),  # Note: needs stdin_text for structured mode
    ]
    
    print("\n" + "="*70)
    print("TESTING WHITELISTED SYS COMMANDS VIA /tools/sys/invoke")
    print("="*70)
    
    results = {}
    for test in tests:
        cmd = test[0]
        args = test[1]
        success = await test_sys_tool(cmd, args)
        results[f"{cmd} {' '.join(args or [])}"] = "✅ Success" if success else "❌ Blocked/Failed"
    
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    for test_name, result in results.items():
        print(f"{result}: {test_name}")

if __name__ == "__main__":
    asyncio.run(main())
