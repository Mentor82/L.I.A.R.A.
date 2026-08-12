#!/usr/bin/env python3
import requests
import json

query = "Hallo, wer bist du?"

body = {
    "session_id": "metadata-test-3",
    "user_id": "wm",
    "message": query,
    "max_tokens": 100,
    "request_source": "assistant"
}

print("Sending query...")
r = requests.post('http://127.0.0.1:8010/chat', json=body, timeout=120)

if r.status_code == 200:
    data = r.json()
    
    metadata = data.get("metadata", {})
    
    print(f"\n✓ Response received ({len(data['response'])} chars)")
    print(f"Metadata keys: {list(metadata.keys())}")
    
    if "inference_metadata" in metadata:
        print(f"\n✓ inference_metadata FOUND!")
        inf_meta = metadata["inference_metadata"]
        print(f"  Content: {inf_meta}")
        if "empty_content_fallback" in inf_meta:
            print(f"✓ empty_content_fallback flag detected: {inf_meta['empty_content_fallback']}")
        else:
            print(f"✗ No empty_content_fallback flag")
    else:
        print(f"\n✗ inference_metadata NOT in metadata dict")
        print(f"  First 5 metadata keys: {list(metadata.keys())[:5]}")
        
else:
    print(f"ERROR: {r.status_code}")
    print(r.text[:500])
