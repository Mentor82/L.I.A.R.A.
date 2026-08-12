#!/bin/sh
set -eu

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required. Alpine hint: apk add curl" >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required. Alpine hint: apk add python3" >&2
  exit 1
fi

BASE_URL="${BASE_URL:-http://127.0.0.1:8010}"
RAW_SESSION_ID="${SESSION_ID:-$(python3 - <<'PY'
import uuid
print(uuid.uuid4().hex[:8])
PY
)}"
case "$RAW_SESSION_ID" in
  demo-memory-*) SESSION_ID="$RAW_SESSION_ID" ;;
  *) SESSION_ID="demo-memory-$RAW_SESSION_ID" ;;
esac
USER_ID="${USER_ID:-wm}"
FIRST_MESSAGE="${FIRST_MESSAGE:-Mein Name ist Mira.}"
SECOND_MESSAGE="${SECOND_MESSAGE:-Wie heisse ich?}"
MAX_TOKENS="${MAX_TOKENS:-512}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/logs/demos}"
mkdir -p "$LOG_DIR"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_PATH="${LOG_PATH:-$LOG_DIR/live-chat-memory-demo-$TIMESTAMP.log}"

TURN_RESPONSE_TEXT=""
TURN_PROGRESS_CSV=""
TURN_MEMORY_EFFECT_DETECTED="false"

write_log() {
  level="$1"
  shift
  msg="$*"
  line="[$(date '+%Y-%m-%d %H:%M:%S')] [$level] $msg"
  printf '%s\n' "$line" | tee -a "$LOG_PATH"
}

json_get() {
  python3 - "$1" "$2" <<'PY'
import json, sys
raw, path = sys.argv[1], sys.argv[2]
try:
    obj = json.loads(raw)
except Exception:
    print("")
    raise SystemExit(0)
value = obj
for part in path.split("."):
    if not part:
        continue
    if isinstance(value, dict):
        value = value.get(part)
    else:
        value = None
    if value is None:
        break
if value is None:
    print("")
elif isinstance(value, (dict, list)):
    print(json.dumps(value, ensure_ascii=False))
else:
    print(str(value))
PY
}

json_payload() {
  python3 - "$SESSION_ID" "$USER_ID" "$1" "$MAX_TOKENS" <<'PY'
import json, sys
payload = {
    "session_id": sys.argv[1],
    "user_id": sys.argv[2],
    "message": sys.argv[3],
    "max_tokens": int(sys.argv[4]),
}
print(json.dumps(payload, ensure_ascii=False))
PY
}

color_echo() {
  color="$1"
  shift
  code="0"
  case "$color" in
    red) code="31" ;;
    yellow) code="33" ;;
    cyan) code="36" ;;
  esac
  printf '\n\033[%sm%s\033[0m\n' "$code" "$*"
}

invoke_live_stream_turn() {
  turn="$1"
  message="$2"
  payload="$(json_payload "$message")"
  current_event=""
  response_text=""
  progress_csv=""
  memory_effect="false"
  tmp_file="$(mktemp)"

  write_log INFO "TURN $turn request: $message"

  if ! curl -sS -N \
    -H 'Accept: text/event-stream' \
    -H 'Content-Type: application/json' \
    -X POST "$BASE_URL/chat/stream" \
    --data "$payload" >"$tmp_file"; then
    rm -f "$tmp_file"
    return 1
  fi

  while IFS= read -r line || [ -n "$line" ]; do
    [ -z "$line" ] && continue
    case "$line" in
      event:*)
        current_event=$(printf '%s' "$line" | sed 's/^event:[[:space:]]*//')
        continue
        ;;
      data:*)
        raw_data=$(printf '%s' "$line" | sed 's/^data:[[:space:]]*//')
        ;;
      *)
        continue
        ;;
    esac

    case "$current_event" in
      progress)
        stage="$(json_get "$raw_data" "stage")"
        msg="$(json_get "$raw_data" "message")"
        context_mode="$(json_get "$raw_data" "metadata.context_mode")"
        [ -z "$stage" ] && stage="progress"
        if [ -z "$progress_csv" ]; then
          progress_csv="$stage"
        else
          progress_csv="$progress_csv, $stage"
        fi
        if [ "$stage" = "memory_effect_detected" ]; then
          memory_effect="true"
        fi
        if [ -n "$context_mode" ]; then
          write_log INFO "TURN $turn progress: $stage -> $msg | mode=$context_mode"
        else
          write_log INFO "TURN $turn progress: $stage -> $msg"
        fi
        ;;
      heartbeat)
        stage="$(json_get "$raw_data" "stage")"
        elapsed_ms="$(json_get "$raw_data" "elapsed_ms")"
        [ -z "$stage" ] && stage="running"
        write_log INFO "TURN $turn heartbeat: stage=$stage elapsed_ms=$elapsed_ms"
        ;;
      chunk)
        idx="$(json_get "$raw_data" "index")"
        text="$(json_get "$raw_data" "text")"
        response_text="${response_text}${text}"
        write_log INFO "TURN $turn chunk[$idx]: $text"
        ;;
      final)
        mode="$(json_get "$raw_data" "metadata.context_debug.mode")"
        provider="$(json_get "$raw_data" "llm_provider")"
        model="$(json_get "$raw_data" "llm_model")"
        [ -z "$mode" ] && mode="UNKNOWN"
        [ -z "$provider" ] && provider="unknown"
        [ -z "$model" ] && model="unknown"
        write_log INFO "TURN $turn final: mode=$mode provider=$provider model=$model"
        ;;
      done)
        write_log INFO "TURN $turn done"
        ;;
      *)
        write_log INFO "TURN $turn event '$current_event': $raw_data"
        ;;
    esac
  done <"$tmp_file"

  rm -f "$tmp_file"
  TURN_RESPONSE_TEXT="$response_text"
  TURN_PROGRESS_CSV="$progress_csv"
  TURN_MEMORY_EFFECT_DETECTED="$memory_effect"
  return 0
}

write_log INFO "Live chat memory demo started"
write_log INFO "BaseUrl=$BASE_URL SessionId=$SESSION_ID UserId=$USER_ID"
write_log INFO "Log file: $LOG_PATH"

if ! invoke_live_stream_turn 1 "$FIRST_MESSAGE"; then
  write_log ERROR "TURN 1 failed"
  color_echo red "Demo log: $LOG_PATH"
  exit 1
fi
turn1_progress_csv="$TURN_PROGRESS_CSV"
write_log INFO "TURN 1 response_text: $TURN_RESPONSE_TEXT"

if ! invoke_live_stream_turn 2 "$SECOND_MESSAGE"; then
  write_log ERROR "TURN 2 failed"
  color_echo red "Demo log: $LOG_PATH"
  exit 1
fi
turn2_progress_csv="$TURN_PROGRESS_CSV"
turn2_response_text="$TURN_RESPONSE_TEXT"
turn2_memory_effect="$TURN_MEMORY_EFFECT_DETECTED"
write_log INFO "TURN 2 response_text: $TURN_RESPONSE_TEXT"

write_log INFO "$(printf 'SUMMARY:\nSessionId: %s\nTurn1 progress: %s\nTurn2 progress: %s\nTurn2 memory effect detected: %s' \
  "$SESSION_ID" "$turn1_progress_csv" "$turn2_progress_csv" "$turn2_memory_effect")"

if [ "$turn2_memory_effect" = "true" ] || printf '%s' "$turn2_response_text" | grep -q "Mira"; then
  write_log SUCCESS "Memory effect observed in second turn."
  color_echo cyan "Demo log: $LOG_PATH"
  exit 0
fi

write_log ERROR "No memory effect detected in second turn."
color_echo yellow "Demo log: $LOG_PATH"
exit 2
