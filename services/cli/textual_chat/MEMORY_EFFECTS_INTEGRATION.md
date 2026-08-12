# Memory Effects Integration in Textual Chat UI

## Overview
The Textual Chat CLI now supports live SSE streaming with memory effect detection. When using `stream` mode, the UI will:
1. Show real-time progress of orchestration (planning)
2. Detect and display memory effects (`✨ Memory effect detected`)
3. Show context mode in the Runtime sidebar (MEMORY or DEFAULT)
4. Display actual context mode used in the final response

## Features Added

### 1. Progress Callback Support in Client
The `LiaraApiClient.send_stream()` method now accepts an optional `progress_callback` parameter:

```python
async def send_stream(
    self,
    message: str,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> ChatReply:
    """Send message via streaming endpoint with optional progress callback."""
```

### 2. Memory Effect Display
When `memory_effect_detected` stage is received, the activity bar shows:
- `✨ Memory effect detected (context_mode=MEMORY)`

### 3. Runtime Metrics Updated
The Runtime sidebar now displays:
- `memory: ✨ MEMORY` (with sparkle emoji when using memory)
- `memory: ○ DEFAULT` (when not using memory)

## Usage

### Default Mode
```bash
cd /ai/LIARA
python -m services.cli.textual_chat
```

### Stream Mode (Recommended)
Within the Textual Chat:
- Press `Ctrl+S` to toggle to `stream` mode
- Type a message and press Enter
- Watch the Activity bar for memory effect detection
- See the Runtime metrics update with context_mode

### Multi-Turn Conversation
1. **Turn 1**: "Mein Name ist Mira."
2. **Turn 2**: "Wie heisse ich?" → Should show `✨ Memory effect detected` in activity bar
3. Check Runtime sidebar for `memory: ✨ MEMORY`

## Implementation Details

### Progress Events Handled
- `accepted`: Message accepted by API
- `orchestration_complete`: Planning phase complete
- `memory_effect_detected`: Memory context detected (shows emoji alert)
- `done`: Response generation complete

### Context Mode Values
- `MEMORY`: Session history was used in context
- `DEFAULT`: No session memory used
- `-`: Unknown or not computed

## Files Modified
- `services/cli/textual_chat/client.py`: Added progress_callback parameter to send_stream()
- `services/cli/textual_chat/app.py`: 
  - Added progress callback handling with memory effect detection
  - Enhanced runtime metrics display with memory indicator
  - Updated activity bar to show memory effects in real-time

## Testing
```bash
# Run live integration tests with memory effects
$env:RUN_LIVE_CHAT_STREAM_MEMORY_TESTS=1
python -m pytest tests/integration/test_chat_stream_memory_effect_live.py -v
```

Both the CLI and HTTP API tests confirm memory effect detection works correctly.
