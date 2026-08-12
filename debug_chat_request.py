#!/usr/bin/env python3
"""Quick debug for ChatRequest validation."""

import asyncio
import httpx
import json

API_BASE = "http://127.0.0.1:8010"

async def debug_chat_request():
    """Test chat endpoint with detailed error reporting."""
    payload = {
        "message": "Mein Name ist TestUser.",
        "session_id": "test-session-debug",
        "user_id": "test-user-debug",
        "max_tokens": 256,
    }
    
    print(f"Request payload:\n{json.dumps(payload, indent=2)}\n")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{API_BASE}/chat", json=payload)
            print(f"Status: {resp.status_code}")
            print(f"Response:\n{json.dumps(resp.json(), indent=2)}\n")
            resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"Error {e.response.status_code}:")
        try:
            print(json.dumps(e.response.json(), indent=2))
        except:
            print(e.response.text)
    except Exception as e:
        print(f"Exception: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(debug_chat_request())
