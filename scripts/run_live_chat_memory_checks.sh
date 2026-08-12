#!/bin/sh
set -eu

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required. Alpine hint: apk add python3" >&2
  exit 1
fi
if ! command -v pytest >/dev/null 2>&1; then
  echo "pytest is required. Alpine hint: python3 -m pip install pytest" >&2
  exit 1
fi

BASE_URL="${BASE_URL:-http://127.0.0.1:8010}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
USER_ID="${USER_ID:-wm}"
MAX_TOKENS="${MAX_TOKENS:-512}"
RAW_SESSION_ID="${SESSION_ID:-$(python3 - <<'PY'
import uuid
print(uuid.uuid4().hex[:8])
PY
)}"
case "$RAW_SESSION_ID" in
  demo-memory-*) SESSION_ID="$RAW_SESSION_ID" ;;
  *) SESSION_ID="demo-memory-$RAW_SESSION_ID" ;;
esac
SKIP_DEMO="${SKIP_DEMO:-0}"
SKIP_PYTEST="${SKIP_PYTEST:-0}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

step() {
  printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"
}

step "Repo root: $REPO_ROOT"
step "Base URL: $BASE_URL"
step "Session ID: $SESSION_ID"

if [ "$SKIP_DEMO" != "1" ]; then
  step "Running live chat demo script"
  BASE_URL="$BASE_URL" \
  SESSION_ID="$SESSION_ID" \
  USER_ID="$USER_ID" \
  MAX_TOKENS="$MAX_TOKENS" \
  sh ./scripts/live_chat_memory_demo.sh
fi

if [ "$SKIP_PYTEST" != "1" ]; then
  step "Running live pytest stream memory check"
  RUN_LIVE_CHAT_STREAM_MEMORY_TESTS=1 \
  LIARA_API_BASE_URL="$BASE_URL" \
  "$PYTHON_BIN" -m pytest tests/integration/test_chat_stream_memory_effect_live.py -q
fi

step "All live chat memory checks passed"
printf '\n\033[36mSession ID: %s\033[0m\n' "$SESSION_ID"
