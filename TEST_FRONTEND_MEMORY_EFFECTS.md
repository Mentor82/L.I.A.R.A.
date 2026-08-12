# Frontend Memory Effects Integration Test Guide

## Overview
This guide provides step-by-step instructions to test memory effect detection across all integrated LIARA frontends.

## Prerequisites
- All LIARA services running (API, Orchestrator, Memory Service)
- At least one session with multi-turn conversation history
- Access to all frontend applications

## Test Case: Memory Recall Detection

### Scenario
1. Start a new chat session
2. Provide personal information (e.g., "My name is Alice, I like Python")
3. End conversation
4. Start a new session
5. Ask "Who am I?" or refer to information from previous session
6. Observe memory effect indicator

---

## Frontend Testing

### 1. Web UI (http://localhost:3000)

**Test Steps**:
```bash
cd frontend/web-ui
npm run dev
# Navigate to http://localhost:3000
```

**What to Look For**:
- **First Turn**: Chat normally, introduce yourself
  - Stream status bar should show: `STREAM CHUNKING`
  - No memory indicator yet
  
- **After Response**: Check response chips for context mode
  - If `✨ MEMORY` chip appears → Memory context was used
  - If no memory chip → `DEFAULT` mode (no prior context)

- **Second Session (New browser tab)**: 
  - Create new session (different session_id)
  - Type message related to first session
  - Watch stream status bar for: `STREAM MEMORY_EFFECT_DETECTED`
  - Response chips should show: `✨ MEMORY`

**Pass Criteria**: ✅
- Memory badge appears in stream status during processing
- Context mode chip shows in response
- Multi-turn conversation shows awareness of previous context

---

### 2. Textual Chat CLI

**Test Steps**:
```bash
cd /ai/LIARA
python -m services.cli.textual_chat
# Press Ctrl+S to toggle stream mode (should be on by default)
```

**What to Look For**:
- **Activity Bar**: During processing, watch for `✨` emoji when memory detected
- **Runtime Sidebar**: Shows `memory: ✨ MEMORY` or `memory: ○ DEFAULT`
- **Console Output**: Progress events show stage names

**Pass Criteria**: ✅
- Memory emoji appears during streaming
- Context mode updates in sidebar
- Multi-turn recall works (names/topics remembered)

---

### 3. GTK UI

**Test Steps**:
```bash
cd frontend/gtk-ui
# Build (if not already built)
mkdir build
cd build
meson setup .. && ninja
# Run
./liara-ui
```

**What to Look For**:
- **Status Output Pane** (bottom): Shows progress messages
  - During memory detection: `✨ Memory Effect Detected\nContext Mode: MEMORY`
  - Normal streaming: `Progress: <stage_name>`
  
- **Chat Bubbles**: Real-time streaming of response text
- **Stream Activity**: Button shows "Streaming..." during active streaming

**Pass Criteria**: ✅
- Status pane updates with memory effect message
- Response streams correctly
- No crashes or memory leaks during streaming

---

## Automated Testing

### Integration Test
```bash
# Run live memory effect tests (requires running services)
cd /ai/LIARA
$env:RUN_LIVE_CHAT_STREAM_MEMORY_TESTS=1
python -m pytest tests/integration/test_chat_stream_memory_effect_live.py -v
```

**Expected Output**:
```
tests/integration/test_chat_stream_memory_effect_live.py::test_live_chat_stream_reports_progress_and_memory_effect PASSED
tests/integration/test_chat_stream_memory_effect_live.py::test_live_chat_stream_complex_multi_turn_flow PASSED
```

---

## Debug Checklist

### If Memory Effects Not Showing

**1. Backend Verification**:
```bash
# Check API is streaming events correctly
curl -N -X POST http://localhost:8010/chat/stream \
  -H "Content-Type: application/json" \
  -d '{
    "session_id":"test-session",
    "user_id":"test-user",
    "message":"Who am I?"
  }' | head -20
```

Look for events like:
```
event: progress
data: {"stage":"memory_effect_detected","message":"...","metadata":{"context_mode":"MEMORY"}}
```

**2. Frontend JavaScript Console** (Web UI):
- Press F12 → Console tab
- Type: `await fetch('http://localhost:8010/chat/stream', {...})`
- Check if progress events parse correctly

**3. Service Health**:
```bash
# Verify Memory Service is running
python -m pytest tests/integration/test_chat_stream_memory_effect_live.py::test_live_chat_stream_reports_progress_and_memory_effect -v -s
```

---

## Expected Behavior Summary

| Frontend | Memory Indicator | Context Mode Display | Multi-turn Recall |
|----------|------------------|----------------------|-------------------|
| Web UI | Badge in stream bar + chips | `✨ MEMORY` chip | ✅ Yes |
| Textual Chat | ✨ emoji in activity bar | Sidebar metric | ✅ Yes |
| GTK UI | Status pane message | Progress text | ✅ Yes |
| Admin TUI | N/A (monitoring only) | Session metadata | ✅ N/A |

---

## Performance Notes

- **TTFT (Time To First Token)**: Displayed in chips/status
- **Stream Progress**: Should see events within 100-500ms
- **Memory Detection Latency**: Usually ~50-200ms during orchestration

---

## Reporting Issues

If memory effects are not showing:

1. **Check Backend Logs**:
   ```bash
   # Verify orchestrator is detecting memory context
   cat logs/orchestrator.log | grep -i memory
   ```

2. **Verify Progress Event Stream**:
   - Memory effects require `memory_effect_detected` progress event
   - Check that SSE streaming is not being buffered

3. **Browser Network Tab** (Web UI):
   - Open DevTools → Network → WS/fetch for `/chat/stream`
   - Look for SSE events in Response tab

---

## Success Metrics

✅ **All Tests Passing When**:
1. Web UI displays memory indicators
2. Textual Chat shows memory emoji
3. GTK UI updates status with memory message
4. Integration tests confirm memory detection
5. Multi-turn conversations show context awareness

🎯 **Target**: All 3 primary frontends showing real-time memory effect detection
