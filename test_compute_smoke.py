#!/usr/bin/env python3
import subprocess
import time
import json
import urllib.request
import sys
import os

os.chdir('c:/ai/LIARA')

server = subprocess.Popen(
    [r'c:/ai/LIARA/.venv/Scripts/python.exe', '-m', 'uvicorn', 'services.api.app:app',
     '--host', '127.0.0.1', '--port', '8030', '--log-level', 'critical'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
)
time.sleep(3)

try:
    # GET /compute/models
    with urllib.request.urlopen('http://127.0.0.1:8030/compute/models', timeout=10) as r:
        models = json.loads(r.read().decode())
    print('[OK] GET /compute/models: available models:', [m['name'] for m in models['models']])
    
    # POST /compute/run
    body = json.dumps({
        'model': 'turbine_power',
        'inputs': {'shaft_speed_rpm': 1500.0, 'torque_nm': 200.0}
    }).encode()
    req = urllib.request.Request(
        'http://127.0.0.1:8030/compute/run',
        data=body,
        headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        result = json.loads(r.read().decode())
    
    power = result['outputs']['power_kw']
    elapsed = result['elapsed_ms']
    print(f'[OK] POST /compute/run: {power} kW computed in {elapsed}ms')
    print('[PASS] All endpoints working with compute.run naming')
    
except Exception as e:
    print(f'[ERROR] {e}')
    sys.exit(1)
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except:
        server.kill()
