#!/usr/bin/env python3
"""E2E test suite for date/time commands."""
import urllib.request
import json
from uuid import uuid4

def test_time_query(message: str) -> dict:
    """Send a time query to /chat and return result."""
    req = urllib.request.Request(
        'http://127.0.0.1:8010/chat',
        data=json.dumps({
            'session_id': str(uuid4()),
            'user_id': 'test_user',
            'message': message
        }).encode('utf-8')
    )
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))

# Test cases
test_cases = [
    "Was ist die aktuelle Zeit?",
    "Nenne mir die UTC-Zeit",
    "Wie spät ist es jetzt?",
    "aktuelle Uhrzeit",
    "What time is it?",
]

print("=" * 70)
print("E2E Time Query Tests")
print("=" * 70)

passed = 0
for i, msg in enumerate(test_cases, 1):
    try:
        result = test_time_query(msg)
        tools = result.get('tools_used', [])
        response = result.get('response', '')
        
        has_sys = 'sys' in tools
        has_datetime = any(x in response for x in ['2026-04', '20', ':', 'UTC', 'Zeit'])
        
        status = '✓' if (has_sys and has_datetime) else '✗'
        print(f"\n{status} Test {i}: {msg}")
        print(f"  Tools: {tools}")
        print(f"  Response (first 150 chars): {response[:150]}")
        
        if has_sys and has_datetime:
            passed += 1
    except Exception as e:
        print(f"\n✗ Test {i}: {msg}")
        print(f"  Error: {e}")

print("\n" + "=" * 70)
print(f"Result: {passed}/{len(test_cases)} tests passed")
print("=" * 70)
