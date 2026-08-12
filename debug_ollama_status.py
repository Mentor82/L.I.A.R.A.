#!/usr/bin/env python3
import requests
import json
import sys

print('=== OLLAMA MODEL STATUS ===\n')

# Test Ollama service
try:
    r = requests.get('http://127.0.0.1:11434/api/tags', timeout=5)
    if r.status_code == 200:
        models = r.json()
        print(f'Available models: {len(models.get("models", []))}')
        for m in models.get('models', [])[:5]:
            print(f'  - {m.get("name")}')
    else:
        print(f'Tags endpoint returned {r.status_code}')
except Exception as e:
    print(f'ERROR accessing Ollama: {e}')

print('\n=== DIRECT OLLAMA TEST (Simple) ===\n')

# Test direct Ollama chat
try:
    prompt = 'Hallo, wer bist du? Antworte kurz.'
    body = {
        'model': 'gpt-oss:120b-cloud',
        'messages': [{'role': 'user', 'content': prompt}],
        'stream': False
    }
    
    print(f'Request to Ollama:')
    print(f'  Model: {body["model"]}')
    print(f'  Message: {prompt}')
    
    r = requests.post('http://127.0.0.1:11434/api/chat',
                     json=body,
                     timeout=30)
    
    if r.status_code == 200:
        resp = r.json()
        content = resp.get('message', {}).get('content', '')
        print(f'\nOllama Response Status: {r.status_code}')
        print(f'Content length: {len(content)}')
        if content:
            print(f'Content: {content[:200]}')
        else:
            print('⚠ WARNING: Empty response from Ollama!')
            print(f'Full response: {json.dumps(resp, indent=2)[:500]}')
    else:
        print(f'ERROR: Status {r.status_code}')
        print(f'Response: {r.text[:500]}')
        
except Exception as e:
    print(f'ERROR: {e}')
    import traceback
    traceback.print_exc()

print('\n=== ORCHESTRATOR TEST ===\n')

# Test via Orchestrator
try:
    body = {
        'session_id': 'debug-ollama',
        'user_id': 'wm',
        'message': 'Hallo!',
        'max_tokens': 64,
        'request_source': 'assistant'
    }
    
    print('Sending to API 8010...')
    r = requests.post('http://127.0.0.1:8010/chat',
                     json=body,
                     timeout=20)
    
    if r.status_code == 200:
        resp = r.json()
        content = resp.get('response', '')
        print(f'API Response Status: {r.status_code}')
        print(f'Response: {content[:200] if content else "(empty)"}')
        
        # Check metadata
        metadata = resp.get('metadata', {})
        if 'empty_response_fallback_applied' in str(metadata):
            print('⚠ Fallback was applied!')
    else:
        print(f'ERROR: Status {r.status_code}')
        
except Exception as e:
    print(f'ERROR: {e}')
