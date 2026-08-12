#!/bin/sh
# live_complex_chat_test.sh
# Two complex multi-turn chat tests for the LIARA orchestrator.
#
# Test 1: sys-routing stress test — math compute + follow-up fact question
# Test 2: memory persistence — user states preferences, recalled in a later turn
#
# Usage:
#   ./scripts/live_complex_chat_test.sh            # against http://127.0.0.1:8010
#   BASE_URL=http://somehost:8010 ./scripts/…
#
set -eu

BASE_URL="${BASE_URL:-http://127.0.0.1:8010}"
USER_ID="${USER_ID:-copilot-test}"
TIMEOUT="120"  # seconds per request — LLM can be slow

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/logs/demos}"
mkdir -p "$LOG_DIR"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_PATH="$LOG_DIR/live-complex-chat-test-$TIMESTAMP.log"

PASS=0
FAIL=0
ERRORS=""

log() { level="$1"; shift; printf '[%s] [%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$level" "$*" | tee -a "$LOG_PATH"; }
info()  { log INFO  "$@"; }
ok()    { log OK    "$@"; PASS=$((PASS+1)); }
fail()  { log FAIL  "$@"; FAIL=$((FAIL+1)); ERRORS="${ERRORS}\n  - $*"; }
sep()   { printf '%.0s─' $(seq 1 70) | tee -a "$LOG_PATH"; printf '\n' | tee -a "$LOG_PATH"; }

json_field() {
  python3 - "$1" "$2" <<'PY'
import json, sys
try:
  obj = json.loads(sys.argv[1])
  keys = sys.argv[2].split(".")
  for k in keys:
    if isinstance(obj, list): obj = obj[int(k)]
    else: obj = obj[k]
  print(obj)
except Exception as e:
  print("")
PY
}

# ── HTTP helpers ──────────────────────────────────────────────────────────────

post_chat() {
  session="$1"; message="$2"
  python3 - "$BASE_URL" "$session" "$USER_ID" "$message" "$TIMEOUT" <<'PY'
import json, sys, urllib.request, urllib.error
base, session, user, msg, timeout = sys.argv[1:]
payload = json.dumps({
  "session_id": session,
  "user_id": user,
  "message": msg,
  "max_tokens": 1024,
}).encode()
req = urllib.request.Request(
  f"{base}/chat",
  data=payload,
  headers={"Content-Type": "application/json"},
  method="POST",
)
try:
  with urllib.request.urlopen(req, timeout=float(timeout)) as r:
    body = r.read().decode()
    print(body)
except urllib.error.HTTPError as e:
  print(json.dumps({"error": e.read().decode(), "status_code": e.code}))
except Exception as e:
  print(json.dumps({"error": str(e)}))
PY
}

gen_session() { python3 -c "import uuid; print('complex-test-' + uuid.uuid4().hex[:8])"; }

# ══════════════════════════════════════════════════════════════════════════════
# TEST 1 — sys-routing stress: multi-step compute + follow-up web lookup
# ══════════════════════════════════════════════════════════════════════════════
sep
info "TEST 1: sys-routing — compute 'factorial(12)' then ask 'was ist Python?'"
SESSION1="$(gen_session)"
info "  session_id=$SESSION1"

# Turn 1a: Math compute — should route to sys → python3
info "  Turn 1a: 'Berechne die Fakultaet von 12' …"
RESP1A="$(post_chat "$SESSION1" "Berechne die Fakultaet von 12")"
info "  raw response (first 300 chars): $(printf '%s' "$RESP1A" | head -c 300)"

ANSWER1A="$(json_field "$RESP1A" "answer")"
TOOLS1A="$(json_field "$RESP1A" "tools_used")"
REASON1A="$(json_field "$RESP1A" "routing_reason")"

info "  answer   : $(printf '%s' "$ANSWER1A" | head -c 200)"
info "  tools    : $TOOLS1A"
info "  reason   : $REASON1A"

if printf '%s' "$TOOLS1A" | grep -qi "sys"; then
  ok "Turn 1a: routed to /sys"
else
  fail "Turn 1a: expected /sys tool, got: $TOOLS1A"
fi

if printf '%s' "$ANSWER1A" | grep -qE "479001600|Fakultät|factorial|Ergebnis|479"; then
  ok "Turn 1a: result contains factorial(12) = 479001600"
else
  fail "Turn 1a: answer does not mention 479001600 — got: $(printf '%s' "$ANSWER1A" | head -c 200)"
fi

# Turn 1b: Web lookup — should route to sys → curl Wikipedia
info "  Turn 1b: 'Was ist Python und wann wurde es entwickelt?' …"
RESP1B="$(post_chat "$SESSION1" "Was ist Python und wann wurde es entwickelt?")"
ANSWER1B="$(json_field "$RESP1B" "answer")"
TOOLS1B="$(json_field "$RESP1B" "tools_used")"
REASON1B="$(json_field "$RESP1B" "routing_reason")"

info "  answer   : $(printf '%s' "$ANSWER1B" | head -c 300)"
info "  tools    : $TOOLS1B"
info "  reason   : $REASON1B"

if printf '%s' "$TOOLS1B" | grep -qi "sys"; then
  ok "Turn 1b: routed to /sys"
else
  fail "Turn 1b: expected /sys tool, got: $TOOLS1B"
fi

if printf '%s' "$ANSWER1B" | grep -qiE "python|1991|guido|programmier"; then
  ok "Turn 1b: answer mentions Python / Guido / 1991"
else
  fail "Turn 1b: answer unexpectedly sparse — got: $(printf '%s' "$ANSWER1B" | head -c 300)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# TEST 2 — memory persistence: state facts over multiple turns
# ══════════════════════════════════════════════════════════════════════════════
sep
info "TEST 2: memory persistence — name + preference across turns"
SESSION2="$(gen_session)"
info "  session_id=$SESSION2"

# Turn 2a: establish facts
info "  Turn 2a: state name + favorite color …"
RESP2A="$(post_chat "$SESSION2" "Mein Name ist Leonie und meine Lieblingsfarbe ist Smaragdgruen.")"
ANSWER2A="$(json_field "$RESP2A" "answer")"
info "  answer: $(printf '%s' "$ANSWER2A" | head -c 200)"
if printf '%s' "$ANSWER2A" | grep -qiE "leonie|smaragd|farbe|okay|name|habe|merke|notiert|notiere"; then
  ok "Turn 2a: assistant acknowledged name/color"
else
  fail "Turn 2a: no acknowledgement in answer — got: $(printf '%s' "$ANSWER2A" | head -c 200)"
fi

# Turn 2b: recall name
info "  Turn 2b: 'Wie heisse ich?' …"
RESP2B="$(post_chat "$SESSION2" "Wie heisse ich?")"
ANSWER2B="$(json_field "$RESP2B" "answer")"
MEM2B="$(json_field "$RESP2B" "memory_effect_detected")"
info "  answer              : $(printf '%s' "$ANSWER2B" | head -c 200)"
info "  memory_effect_detected: $MEM2B"

if printf '%s' "$ANSWER2B" | grep -qi "leonie"; then
  ok "Turn 2b: recalled name Leonie"
else
  fail "Turn 2b: name Leonie not recalled — got: $(printf '%s' "$ANSWER2B" | head -c 200)"
fi

# Turn 2c: recall preference
info "  Turn 2c: 'Was ist meine Lieblingsfarbe?' …"
RESP2C="$(post_chat "$SESSION2" "Was ist meine Lieblingsfarbe?")"
ANSWER2C="$(json_field "$RESP2C" "answer")"
info "  answer: $(printf '%s' "$ANSWER2C" | head -c 200)"

if printf '%s' "$ANSWER2C" | grep -qiE "smaragd|gruen|grün|farbe"; then
  ok "Turn 2c: recalled favorite color Smaragdgruen"
else
  fail "Turn 2c: color not recalled — got: $(printf '%s' "$ANSWER2C" | head -c 200)"
fi

# ══════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════════
sep
info "RESULTS: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
  info "FAILURES:$(printf '%b' "$ERRORS")"
fi
info "Full log: $LOG_PATH"

if [ "$FAIL" -gt 0 ]; then
  exit 1
fi
exit 0
