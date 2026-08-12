import json
import pathlib
import requests

prompt = pathlib.Path("c:/ai/LIARA/scripts/tmp_capacitor_prompt.txt").read_text(encoding="utf-8")
payload = {
    "session_id": "web-test-rc-50hz",
    "user_id": "wm",
    "message": prompt,
    "max_tokens": 2048,
}

resp = requests.post("http://127.0.0.1:8010/chat", json=payload, timeout=180)
print("status", resp.status_code)
resp.raise_for_status()
data = resp.json()

print("response_len", len(data.get("response") or ""))
print("tools_used", data.get("tools_used"))
artifacts = data.get("artifacts") or []
print("artifacts_count", len(artifacts))
print("response_preview")
print((data.get("response") or "")[:700])
print("artifacts_json")
print(json.dumps(artifacts, ensure_ascii=False, indent=2))
