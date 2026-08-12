#!/usr/bin/env python3
import requests

# List models
r = requests.get('http://127.0.0.1:11434/api/tags')
models = r.json()

print('Available Ollama models:')
for m in models.get('models', []):
    name = m.get('name', 'unknown')
    size = m.get('size', 0)
    print(f'  - {name} ({size:,} bytes)')

# Check if gpt-oss:120b-cloud exists
target = 'gpt-oss:120b-cloud'
found = any(m.get('name') == target for m in models.get('models', []))
print(f'\nLooking for: {target}')
print(f'Found: {found}')

# Show which model actually has 120b or cloud in name
print('\nModels with "120b" or "cloud" or "gpt" in name:')
for m in models.get('models', []):
    name = m.get('name', '')
    if any(x in name.lower() for x in ['120b', 'cloud', 'gpt']):
        print(f'  - {name}')
