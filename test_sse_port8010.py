#!/usr/bin/env python
import requests
import uuid

url = 'http://127.0.0.1:8010/chat/stream'
payload = {
    'user_query': 'Hello world',
    'session_id': str(uuid.uuid4()),
    'user_id': 'test_user',
    'message': 'Hello world'
}

print("Testing SSE stream on Port 8010...")
print(f"URL: {url}")
print()

try:
    response = requests.post(url, json=payload, stream=True, timeout=30)
    print(f'Status: {response.status_code}')
    print()
    event_count = 0
    for line in response.iter_lines():
        if line:
            line_str = line.decode('utf-8') if isinstance(line, bytes) else line
            print(line_str)
            if line_str.startswith('event:'):
                event_count += 1
    print()
    print(f"Total events received: {event_count}")
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
