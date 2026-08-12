#!/usr/bin/env python3
"""Check trace and routing for time query."""
import urllib.request
import json
from uuid import uuid4

req = urllib.request.Request(
    'http://127.0.0.1:8010/chat',
    data=json.dumps({
        'session_id': str(uuid4()),
        'user_id': 'test_user',
        'message': 'Nenne mir die aktuelle UTC Zeit'
    }).encode('utf-8')
)
req.add_header('Content-Type', 'application/json')

try:
    with urllib.request.urlopen(req) as resp:
        data = resp.read().decode('utf-8')
        obj = json.loads(data)
        
        trace = (obj.get('metadata') or {}).get('execution_trace') or []
        sel_t = next((t for t in trace if t.get('to') == 'tool_selection'), {})
        exe_t = next((t for t in trace if t.get('to') == 'tool_execution'), {})
        
        print(f'✓ Zeit-Query erfolgreich!')
        print(f'RUN_ID: {obj.get("run_id")}')
        print(f'TOOLS_USED: {obj.get("tools_used")}')
        
        print(f'\nTOOL_SELECTION metadata:')
        sel_meta = sel_t.get('metadata') or {}
        print(f'  selected_tools: {sel_meta.get("selected_tools")}')
        print(f'  reason: {sel_meta.get("reason")}')
        
        print(f'\nTOOL_EXECUTION metadata:')
        exe_meta = exe_t.get('metadata') or {}
        print(f'  tool_statuses: {exe_meta.get("tool_statuses")}')
        print(f'  failed_tools: {exe_meta.get("failed_tools")}')
        if exe_meta.get("tool_errors"):
            print(f'  tool_errors: {exe_meta.get("tool_errors")}')
            
        print(f'\nRESPONSE_PREVIEW: {(obj.get("response") or "")[:300]}')
except Exception as e:
    print(f'✗ Fehler: {e}')
    import traceback
    traceback.print_exc()
