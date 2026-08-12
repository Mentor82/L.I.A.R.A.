#!/usr/bin/env python3
"""Test response schema from manual tool invocation."""

import json
import httpx
import asyncio

async def test_tool_invoke():
    """Test POST /tools/sys/invoke and validate response schema."""
    
    payload = {
        "parameters": {
            "command": "curl",
            "args": ["-s", "-I", "https://example.com"]
        },
        "timeout_seconds": 10
    }
    
    print("=" * 60)
    print("Testing POST /tools/sys/invoke")
    print("=" * 60)
    print(f"\nPayload:\n{json.dumps(payload, indent=2)}\n")
    
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            response = await client.post(
                "http://127.0.0.1:8010/tools/sys/invoke",
                json=payload,
            )
            
            print(f"Status Code: {response.status_code}")
            print(f"Headers: {dict(response.headers)}\n")
            
            data = response.json()
            print(f"Response Body:\n{json.dumps(data, indent=2)}\n")
            
            # Validate schema
            required_fields = ["tool_name", "status", "output"]
            missing = [f for f in required_fields if f not in data]
            
            if missing:
                print(f"✗ INVALID: Missing required fields: {missing}")
            else:
                print("✓ VALID Response Schema")
                print(f"  - tool_name: {data['tool_name']}")
                print(f"  - status: {data['status']}")
                print(f"  - has output: {bool(data.get('output'))}")
                print(f"  - has error: {bool(data.get('error'))}")
                print(f"  - execution_ms: {data.get('execution_ms')}")
            
        except Exception as e:
            print(f"✗ ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(test_tool_invoke())
