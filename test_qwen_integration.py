#!/usr/bin/env python3
import asyncio
import json
import httpx
from datetime import datetime

async def test_chat_api():
    """Test /chat endpoint with qwen3-next:80b-cloud model."""
    url = "http://127.0.0.1:8010/chat"
    
    request_data = {
        "session_id": f"test_session_{datetime.now().timestamp()}",
        "user_id": "test_user",
        "message": "Hallo! Wie geht es dir?"
    }
    
    print("=" * 60)
    print(f"Testing {url}")
    print(f"Request: {json.dumps(request_data, indent=2)}")
    print("=" * 60)
    
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            response = await client.post(url, json=request_data)
            print(f"Status: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("content", "")
                metadata = result.get("metadata", {})
                
                print(f"\n✓ Response received ({len(content)} chars)")
                print(f"\nContent (first 400 chars):\n{content[:400]}")
                
                if metadata:
                    print(f"\nMetadata:")
                    inference_meta = metadata.get("inference_metadata", {})
                    print(f"  - inference_metadata: {inference_meta}")
                    if metadata.get("state_final"):
                        print(f"  - state_final: {metadata['state_final']}")
                    if metadata.get("validation"):
                        print(f"  - validation: {metadata['validation']}")
                        
                return True
            else:
                print(f"✗ Error: {response.text}")
                return False
                
    except Exception as e:
        print(f"✗ Exception: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_chat_api())
    exit(0 if success else 1)
