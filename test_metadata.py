#!/usr/bin/env python3
import requests
import json

query = "Hallo, wer bist du?"

body = {
    "session_id": "metadata-test",
    "user_id": "wm",
    "message": query,
    "max_tokens": 100,
    "request_source": "assistant"
}

print("Sending query...")
r = requests.post('http://127.0.0.1:8010/chat', json=body, timeout=120)

if r.status_code == 200:
    data = r.json()
    response_text = data.get("response", "")
    metadata = data.get("metadata", {})
    llm_generation = data.get("llm_generation", {})
    
    print(f"\nResponse: {response_text[:150]}")
    print(f"\nLLM Generation keys: {list(llm_generation.keys())}")
    
    if "inference_metadata" in llm_generation:
        print(f"✓ inference_metadata FOUND!")
        print(f"  Content: {llm_generation['inference_metadata']}")
    else:
        print(f"✗ inference_metadata MISSING")
        
    # Also check if fallback was applied
    if "empty_content_fallback" in llm_generation.get("inference_metadata", {}):
        print(f"✓ empty_content_fallback flag detected")
    else:
        print(f"✗ No empty_content_fallback flag")
else:
    print(f"ERROR: {r.status_code}")
    print(r.text[:300])
