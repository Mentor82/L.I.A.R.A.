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
  demo-scheduler-*) SESSION_ID="$RAW_SESSION_ID" ;;
  *) SESSION_ID="demo-scheduler-$RAW_SESSION_ID" ;;
esac
USER_ID="${USER_ID:-wm}"
MAX_TOKENS="${MAX_TOKENS:-1200}"
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/logs/demos}"
mkdir -p "$LOG_DIR"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
LOG_PATH="${LOG_PATH:-$LOG_DIR/live-chat-scheduler-demo-$TIMESTAMP.log}"

TASK_PROMPT="${TASK_PROMPT:-\
Beispielprojekt: Modularer Task-Scheduler mit Plugin-System.\n\
Erstelle einen klaren Projektentwurf in Python mit folgenden Punkten:\n\
- Aufgaben (Tasks) verwalten\n\
- unterschiedliche Task-Typen ueber Plugins laden\n\
- Scheduler-Kern\n\
- Event-System\n\
- Logs, State und Config getrennt halten\n\
- sauber modular aufgebaut\n\
Nicht zu leicht, nicht zu komplex.\n\
Bitte gib:\n\
1) Modulstruktur (Dateibaum),\n\
2) kurze Responsibilities je Modul,\n\
3) zentralen Ablauf (Task -> Scheduler -> Event -> Logging/State),\n\
4) kleines lauffaehiges Code-Skelett (nur Kern, kein Overengineering).\
}"

TURN_RESPONSE_TEXT=""

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
for part in path.split('.'):
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

invoke_live_stream_turn() {
  message="$1"
  payload="$(json_payload "$message")"
  current_event=""
  response_text=""
  tmp_file="$(mktemp)"

  write_log INFO "TURN request sent"

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
        if [ -n "$context_mode" ]; then
          write_log INFO "progress: $stage -> $msg | mode=$context_mode"
        else
          write_log INFO "progress: $stage -> $msg"
        fi
        ;;
      heartbeat)
        stage="$(json_get "$raw_data" "stage")"
        elapsed_ms="$(json_get "$raw_data" "elapsed_ms")"
        write_log INFO "heartbeat: stage=$stage elapsed_ms=$elapsed_ms"
        ;;
      chunk)
        idx="$(json_get "$raw_data" "index")"
        text="$(json_get "$raw_data" "text")"
        response_text="${response_text}${text}"
        write_log INFO "chunk[$idx] received"
        ;;
      final)
        mode="$(json_get "$raw_data" "metadata.context_debug.mode")"
        provider="$(json_get "$raw_data" "llm_provider")"
        model="$(json_get "$raw_data" "llm_model")"
        [ -z "$mode" ] && mode="UNKNOWN"
        [ -z "$provider" ] && provider="unknown"
        [ -z "$model" ] && model="unknown"
        write_log INFO "final: mode=$mode provider=$provider model=$model"
        ;;
      done)
        write_log INFO "done"
        ;;
      *)
        write_log INFO "event '$current_event'"
        ;;
    esac
  done <"$tmp_file"

  rm -f "$tmp_file"
  TURN_RESPONSE_TEXT="$response_text"
  return 0
}

validate_response() {
  python3 - "$1" <<'PY'
import sys
text = (sys.argv[1] or "").lower()
checks = [
    "plugin",
    "scheduler",
    "event",
    "config",
    "state",
    "log",
    "task",
    "modul",
]
hits = [kw for kw in checks if kw in text]
print(f"hits={len(hits)} matched={','.join(hits)}")
# Require at least 6/8 key concepts to count as a strong answer.
raise SystemExit(0 if len(hits) >= 6 else 2)
PY
}

write_log INFO "Live chat scheduler demo started"
write_log INFO "BaseUrl=$BASE_URL SessionId=$SESSION_ID UserId=$USER_ID"
write_log INFO "Log file: $LOG_PATH"
write_log INFO "Prompt: modular task scheduler with plugin system"

if ! invoke_live_stream_turn "$TASK_PROMPT"; then
  write_log ERROR "Stream call failed"
  printf '\n\033[31mDemo log: %s\033[0m\n' "$LOG_PATH"
  exit 1
fi

write_log INFO "Response length: $(printf '%s' "$TURN_RESPONSE_TEXT" | wc -c | tr -d ' ') chars"
write_log INFO "Response preview: $(printf '%s' "$TURN_RESPONSE_TEXT" | tr '\n' ' ' | cut -c1-280)"

if validate_response "$TURN_RESPONSE_TEXT" >>"$LOG_PATH" 2>&1; then
  write_log SUCCESS "Scheduler architecture response includes required concepts."
  printf '\n\033[36mDemo log: %s\033[0m\n' "$LOG_PATH"
  exit 0
fi

write_log ERROR "Response did not include enough required scheduler concepts."
printf '\n\033[33mDemo log: %s\033[0m\n' "$LOG_PATH"
exit 2
