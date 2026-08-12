"""
live_complex_chat_test.py
Two complex multi-turn live chat tests for the LIARA orchestrator.

Test 1 — sys-routing stress:
  Turn 1a: math compute (factorial 12)   → expects /sys + python3
  Turn 1b: web fact-lookup (Python lang) → expects /sys + curl/wikipedia

Test 2 — memory persistence:
  Turn 2a: state name "Leonie" + color "Smaragdgruen"
  Turn 2b: recall name
  Turn 2c: recall color

Usage:
  python scripts/live_complex_chat_test.py
  BASE_URL=http://somehost:8010 python scripts/live_complex_chat_test.py
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8010").rstrip("/")
USER_ID  = os.getenv("USER_ID", "copilot-test")
TIMEOUT  = float(os.getenv("CHAT_TIMEOUT", "180"))

PASS_COUNT = 0
FAIL_COUNT = 0
FAILURES: list[str] = []


# ── helpers ────────────────────────────────────────────────────────────────────

def ts() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")

def log(level: str, msg: str) -> None:
    print(f"[{ts()}] [{level}] {msg}", flush=True)

def info(msg: str)  -> None: log("INFO", msg)
def ok(label: str)  -> None:
    global PASS_COUNT
    PASS_COUNT += 1
    log("OK  ", label)

def fail(label: str) -> None:
    global FAIL_COUNT
    FAIL_COUNT += 1
    FAILURES.append(label)
    log("FAIL", label)

def sep() -> None:
    print("─" * 70, flush=True)


def post_chat(session_id: str, message: str) -> dict:
    payload = json.dumps({
        "session_id": session_id,
        "user_id": USER_ID,
        "message": message,
        "max_tokens": 1024,
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        log("ERR", f"HTTP {e.code}: {body[:300]}")
        return {"error": body, "status_code": e.code}
    except Exception as e:
        log("ERR", str(e))
        return {"error": str(e)}


def gen_session() -> str:
    return f"complex-test-{uuid.uuid4().hex[:8]}"


def main() -> int:
    global PASS_COUNT, FAIL_COUNT, FAILURES
    PASS_COUNT = 0
    FAIL_COUNT = 0
    FAILURES = []

    # ══════════════════════════════════════════════════════════════════════════════
    # TEST 1 — sys-routing stress: compute + web lookup in same session
    # ══════════════════════════════════════════════════════════════════════════════

    sep()
    info("TEST 1: sys-routing — factorial(12) compute + Python language web lookup")
    session1 = gen_session()
    info(f"  session_id = {session1}")

    # Turn 1a ──────────────────────────────────────────────────────────────────
    info("  Turn 1a: 'Berechne die Fakultaet von 12' ...")
    t0 = time.monotonic()
    r1a = post_chat(session1, "Berechne die Fakultaet von 12")
    elapsed = time.monotonic() - t0
    info(f"  elapsed: {elapsed:.1f}s")

    answer1a = str(r1a.get("response", ""))
    tools1a = str(r1a.get("tools_used", ""))
    reason1a = str(r1a.get("routing_reason", ""))
    info(f"  tools    : {tools1a}")
    info(f"  reason   : {reason1a}")
    info(f"  answer   : {answer1a[:300]}")

    if re.search(r"\bsys\b", tools1a, re.I):
        ok("Turn 1a: routed to /sys")
    else:
        fail(f"Turn 1a: expected /sys tool, got: {tools1a}")

    if re.search(r"479001600|479\s*001\s*600", answer1a):
        ok("Turn 1a: factorial(12) = 479001600 in answer")
    else:
        fail(f"Turn 1a: 479001600 not found in answer — '{answer1a[:200]}'")

    # Turn 1b ──────────────────────────────────────────────────────────────────
    info("  Turn 1b: 'Was ist Python und wann wurde die Programmiersprache entwickelt?' ...")
    t0 = time.monotonic()
    r1b = post_chat(session1, "Was ist Python und wann wurde die Programmiersprache entwickelt?")
    elapsed = time.monotonic() - t0
    info(f"  elapsed: {elapsed:.1f}s")

    answer1b = str(r1b.get("response", ""))
    tools1b = str(r1b.get("tools_used", ""))
    reason1b = str(r1b.get("routing_reason", ""))
    info(f"  tools    : {tools1b}")
    info(f"  reason   : {reason1b}")
    info(f"  answer   : {answer1b[:300]}")

    if re.search(r"\bsys\b", tools1b, re.I):
        ok("Turn 1b: routed to /sys")
    else:
        fail(f"Turn 1b: expected /sys tool, got: {tools1b}")

    if re.search(r"python|1991|guido|programmier", answer1b, re.I):
        ok("Turn 1b: answer contains Python fact (language/1991/Guido)")
    else:
        fail(f"Turn 1b: no Python facts found — '{answer1b[:200]}'")

    # ══════════════════════════════════════════════════════════════════════════════
    # TEST 2 — memory persistence across turns (name + color recall)
    # ══════════════════════════════════════════════════════════════════════════════

    sep()
    info("TEST 2: memory persistence — name 'Leonie' + 'Smaragdgruen' across turns")
    session2 = gen_session()
    info(f"  session_id = {session2}")

    # Turn 2a — establish facts
    info("  Turn 2a: 'Mein Name ist Leonie und meine Lieblingsfarbe ist Smaragdgruen.' ...")
    t0 = time.monotonic()
    r2a = post_chat(session2, "Mein Name ist Leonie und meine Lieblingsfarbe ist Smaragdgruen.")
    elapsed = time.monotonic() - t0
    info(f"  elapsed: {elapsed:.1f}s")

    answer2a = str(r2a.get("response", ""))
    info(f"  answer: {answer2a[:300]}")

    if re.search(r"leonie|smaragd|farbe|name|merke|notiert|okay|verstanden|noted|habe", answer2a, re.I):
        ok("Turn 2a: assistant acknowledged name/color")
    else:
        fail(f"Turn 2a: no acknowledgement — '{answer2a[:200]}'")

    # Turn 2b — recall name
    info("  Turn 2b: 'Wie heisse ich?' ...")
    t0 = time.monotonic()
    r2b = post_chat(session2, "Wie heisse ich?")
    elapsed = time.monotonic() - t0
    info(f"  elapsed: {elapsed:.1f}s")

    answer2b = str(r2b.get("response", ""))
    mem2b = str(r2b.get("memory_effect_detected", ""))
    info(f"  answer                : {answer2b[:300]}")
    info(f"  memory_effect_detected: {mem2b}")

    if re.search(r"leonie", answer2b, re.I):
        ok("Turn 2b: recalled name Leonie ✓")
    else:
        fail(f"Turn 2b: Leonie not recalled — '{answer2b[:200]}'")

    # Turn 2c — recall color
    info("  Turn 2c: 'Was ist meine Lieblingsfarbe?' ...")
    t0 = time.monotonic()
    r2c = post_chat(session2, "Was ist meine Lieblingsfarbe?")
    elapsed = time.monotonic() - t0
    info(f"  elapsed: {elapsed:.1f}s")

    answer2c = str(r2c.get("response", ""))
    info(f"  answer: {answer2c[:300]}")

    if re.search(r"smaragd|gruen|grün|smaragdgrün", answer2c, re.I):
        ok("Turn 2c: recalled Smaragdgruen ✓")
    else:
        fail(f"Turn 2c: Smaragdgruen not recalled — '{answer2c[:200]}'")

    # ══════════════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════════════════════════════
    sep()
    info(f"RESULTS: {PASS_COUNT} passed, {FAIL_COUNT} failed")
    if FAILURES:
        info("FAILURES:")
        for f_ in FAILURES:
            info(f"  - {f_}")

    return 0 if FAIL_COUNT == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
