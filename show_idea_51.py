#!/usr/bin/env python3
import subprocess
import json

result = subprocess.run(['python', 'scripts/build_history.py', 'export'], 
                       capture_output=True, text=True, cwd='c:\\ai\\LIARA')
data = json.loads(result.stdout)
for entry in data:
    if entry['id'] == 51:
        print(json.dumps(entry, indent=2, ensure_ascii=False))
        break
