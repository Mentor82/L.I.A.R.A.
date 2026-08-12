import json
import math
import requests

f = 50.0
omega = 2 * math.pi * f

t_values = [i / 5000.0 for i in range(0, 201)]
u_values = [10.0 * math.sin(omega * t) for t in t_values]

payload = {
    "session_id": "web-test-rc-50hz",
    "sandbox_root": None,
    "params": {
        "query": "Spannung, Strom und Leistung",
        "chart_type": "line",
        "title": "Spannung, Strom und Leistung",
        "x_values": t_values,
        "y_values": u_values,
    },
}

resp = requests.post(
    "http://127.0.0.1:8010/tools/plot_chart/invoke",
    json=payload,
    timeout=180,
)
print("status", resp.status_code)
resp.raise_for_status()
data = resp.json()
print("ok", data.get("ok"))
print("error", data.get("error"))
out = data.get("output") or {}
artifacts = out.get("artifacts") or []
print("artifacts_count", len(artifacts))
print(json.dumps(artifacts, ensure_ascii=False, indent=2))
