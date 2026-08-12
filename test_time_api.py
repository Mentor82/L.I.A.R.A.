#!/usr/bin/env python3
"""Test date/time command support via /chat API."""
import urllib.request
import json
from uuid import uuid4

req = urllib.request.Request(
    'http://127.0.0.1:8010/chat',
    data=json.dumps({
        'session_id': str(uuid4()),
        'user_id': 'test_user',
        'message': 'Was ist die aktuelle Zeit jetzt?'
    }).encode('utf-8')
)
req.add_header('Content-Type', 'application/json')

try:
    with urllib.request.urlopen(req) as resp:
        data = resp.read().decode('utf-8')
        obj = json.loads(data)
        print('✓ Zeit-Query erfolgreich!')
        print(f'RUN_ID: {obj.get("run_id")}')
        print(f'TOOLS_USED: {obj.get("tools_used")}')
        resp_preview = obj.get('response', '')
        if len(resp_preview) > 300:
            resp_preview = resp_preview[:300] + '...'
        print(f'RESPONSE:\n{resp_preview}')
except Exception as e:
    print(f'✗ Fehler: {e}')
    import traceback
    traceback.print_exc()

