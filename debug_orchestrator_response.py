#!/usr/bin/env python3
"""Debug orchestrator response loss."""
import sys
import asyncio
import json
import requests

async def debug_orchestrator_response():
    print("=== Testing Orchestrator LLM Response Processing ===\n")
    
    queries = [
        "Welche Tools kannst du verwenden? (Antworte detailliert)",
        "Hallo! Wer bist du?",
        "Was kannst du machen?"
    ]
    
    print("Testing via direct API HTTP requests:\n")
    
    for i, q in enumerate(queries, 1):
        print(f"--- Query {i}: {q[:50]}... ---")
        
        body = {
            'session_id': f'debug-{i}',
            'user_id': 'wm',
            'message': q,
            'max_tokens': 256,
            'request_source': 'assistant'
        }
        
        try:
            # Non-stream for full response
            r = requests.post('http://127.0.0.1:8010/chat',
                            json=body,
                            timeout=20)
            
            if r.status_code == 200:
                resp = r.json()
                resp_text = resp.get('response', '')
                metadata = resp.get('metadata', {})
                
                print(f"Response length: {len(resp_text)} chars")
                print(f"Response: {resp_text[:150]}")
                
                # Check inference_metadata
                inf_meta = metadata.get('inference_metadata', {})
                if isinstance(inf_meta, dict):
                    if 'empty_content_fallback' in inf_meta:
                        print(f"FALLBACK WAS APPLIED: {inf_meta['empty_content_fallback']}")
                    else:
                        print(f"No empty_content_fallback flag in inference_metadata")
                        print(f"  Inference metadata keys: {list(inf_meta.keys())[:10]}")
                else:
                    print(f"Inference metadata is not dict: {type(inf_meta)}")
            else:
                print(f"ERROR: Status {r.status_code}")
                
        except Exception as e:
            print(f"ERROR: {e}")
        
        print()

if __name__ == '__main__':
    asyncio.run(debug_orchestrator_response())
