#!/usr/bin/env python
import requests
import uuid
import time
import sys

url = 'http://127.0.0.1:8010/chat/stream'
payload = {
    'user_query': 'Count from 1 to 100 slowly',
    'session_id': str(uuid.uuid4()),
    'user_id': 'test_user',
    'message': 'Count from 1 to 100 slowly'
}

print("Testing SSE stream with longer request (check for heartbeat frequency)...")
print(f"URL: {url}")
print(f"Heartbeat interval should be: 12 seconds (default LIARA_STREAM_HEARTBEAT_SECONDS)")
print()

try:
    start_time = time.time()
    response = requests.post(url, json=payload, stream=True, timeout=180)
    print(f'Status: {response.status_code}\n')
    
    event_count = 0
    heartbeat_count = 0
    last_event_time = start_time
    
    for line in response.iter_lines():
        if line:
            line_str = line.decode('utf-8') if isinstance(line, bytes) else line
            current_time = time.time()
            elapsed_since_start = current_time - start_time
            
            if line_str.startswith('event:'):
                event_type = line_str.replace('event: ', '').strip()
                if event_type == 'heartbeat':
                    heartbeat_count += 1
                    time_since_last = current_time - last_event_time
                    print(f"[{elapsed_since_start:.1f}s] EVENT #{event_count+1}: {event_type} (interval: {time_since_last:.1f}s)")
                else:
                    print(f"[{elapsed_since_start:.1f}s] EVENT #{event_count+1}: {event_type}")
                event_count += 1
                last_event_time = current_time
    
    print()
    print(f"Total events: {event_count}")
    print(f"Heartbeats: {heartbeat_count}")
    print(f"Total time: {time.time() - start_time:.1f}s")
    
except Exception as e:
    print(f'Error: {e}')
    import traceback
    traceback.print_exc()
