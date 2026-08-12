import uuid
import httpx

sid = "dbg-hist-status-" + uuid.uuid4().hex[:8]
user = "live-test-user"
base = "http://127.0.0.1:8010"

with httpx.Client(base_url=base, timeout=180.0) as c:
    c.post('/chat', json={'session_id': sid, 'user_id': user, 'message': 'Mein Name ist Mira.', 'max_tokens': 256}).raise_for_status()
    r = c.get('/history', params={'session_id': sid, 'limit': 50, 'include_tool_messages': True})
    r.raise_for_status()
    payload = r.json()
    print('status', payload.get('status'))
    print('items', len(payload.get('items') or []))
