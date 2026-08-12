import uuid
import httpx

sid = "dbg-hist-" + uuid.uuid4().hex[:8]
user = "live-test-user"
base = "http://127.0.0.1:8010"

with httpx.Client(base_url=base, timeout=180.0) as c:
    c.post('/chat', json={'session_id': sid, 'user_id': user, 'message': 'Mein Name ist Mira.', 'max_tokens': 256}).raise_for_status()
    c.post('/chat', json={'session_id': sid, 'user_id': user, 'message': 'Wie heisse ich?', 'max_tokens': 256}).raise_for_status()
    r = c.get('/history', params={'session_id': sid, 'limit': 50, 'include_tool_messages': True})
    r.raise_for_status()
    items = r.json().get('items') or []
    print('session', sid)
    print('items', len(items))
    for item in items[-6:]:
        role = item.get('role')
        content = str(item.get('content') or '')
        print(f"{role}: {content[:120]}")
