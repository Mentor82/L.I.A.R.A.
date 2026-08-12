import uuid
import httpx

base = 'http://127.0.0.1:8010'
sid = 'dbg-utc-' + uuid.uuid4().hex[:8]
uid = 'live-test-user'

with httpx.Client(base_url=base, timeout=120.0) as c:
    r = c.post('/chat', json={
        'session_id': sid,
        'user_id': uid,
        'message': 'Wie spaet ist es gerade in UTC?',
        'max_tokens': 256,
    })
    r.raise_for_status()
    data = r.json()
    print('session', sid)
    print('answer_preview', str(data.get('response') or '')[:180])
    s = c.get('/admin/sys-audit/summary', params={'limit': 10})
    s.raise_for_status()
    items = s.json().get('items') or []
    for item in items:
        if item.get('session_id') == sid:
            print('audit_command', item.get('command'))
            print('audit_tool', item.get('tool_name'))
            break
    else:
        print('audit_command', '<not-found>')
