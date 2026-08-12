#!/usr/bin/env python3
import requests
import json

query = "Hallo, wer bist du?"

body = {
    "session_id": "metadata-test-2",
    "user_id": "wm",
    "message": query,
    "max_tokens": 100,
    "request_source": "assistant"
}

print("Sending query...")
r = requests.post('http://127.0.0.1:8010/chat', json=body, timeout=120)

if r.status_code == 200:
    data = r.json()
    
    print("\n=== FULL RESPONSE STRUCTURE ===")
    print(f"Top-level keys: {list(data.keys())}")
    
    # Print indented JSON
    print("\nFull response (first 2000 chars):")
    full_json = json.dumps(data, indent=2, ensure_ascii=False)
    print(full_json[:2000])
    
else:
    print(f"ERROR: {r.status_code}")
    print(r.text[:500])
