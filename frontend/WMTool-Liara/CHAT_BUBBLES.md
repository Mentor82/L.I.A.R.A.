# Modern Chat Bubbles in LIARA GTK UI

## Overview

The chat UI has been refactored to use a modern **ListBox-based bubble layout** with clean separation between user and assistant messages, proper text styling, and support for extensible metadata (tool traces, memory tiers, decision records).

## Architecture

### Core Components

1. **GtkListBox Container** (`chat_messages_box`)
   - Replaces flat GtkBox for semantic structure
   - Each message is a `GtkListBoxRow` (non-selectable, non-activatable)
   - Provides native scrolling and focus management

2. **Bubble Layout** (`chat-row-container`)
   - Horizontal flexbox with spacers for positioning
   - Assistant: `[bubble] [spacer]` (left-aligned)
   - User: `[spacer] [bubble]` (right-aligned)
   - Responsive: max-width 65% of container

3. **Bubble Structure** (`chat-bubble`)
   - Vertical box containing:
     - Role label (uppercase, accent-colored)
     - Content box (formatted text with syntax highlighting)
     - Metadata box (tool traces, collapsible details)

## CSS Classes

### Modern Bubble Classes

```css
.chat-bubble                    /* Base bubble styling */
.chat-bubble-assistant          /* Dark theme for assistant (LIARA) */
.chat-bubble-user               /* Blue theme for user */
.chat-bubble-role               /* Role label styling */
.chat-bubble-content            /* Content container */
.chat-bubble-text               /* Text styling */
.chat-bubble-meta               /* Metadata row */
```

### Example CSS

```css
.chat-bubble {
  padding: 10px 12px;
  border-radius: 12px;
  border: 1px solid rgba(34, 211, 238, 0.15);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
  max-width: 65%;
}

.chat-bubble-assistant {
  background: linear-gradient(135deg, rgba(30, 41, 59, 0.6) 0%, rgba(15, 23, 42, 0.5) 100%);
  border-color: rgba(34, 211, 238, 0.25);
}

.chat-bubble-user {
  background: linear-gradient(135deg, rgba(59, 130, 246, 0.18) 0%, rgba(34, 211, 238, 0.12) 100%);
  border-color: rgba(59, 130, 246, 0.3);
}
```

## API Functions

### Append Chat Message

```c
/**
 * Append a chat message to the conversation view.
 *
 * Creates a new ListBoxRow with a bubble, role label, and content.
 * Automatically scrolls to the bottom.
 *
 * @param ui                 LiaraWindow context
 * @param role               Message role (e.g., "You", "LIARA")
 * @param text               Message content
 * @param assistant          TRUE for assistant message, FALSE for user
 * @param stream_label_out   Output: pointer to content label for streaming updates
 * @param meta_box_out       Output: pointer to metadata box for tool traces
 * @return                   Pointer to content_box for future updates
 */
GtkWidget *
append_chat_message(
    LiaraWindow *ui,
    const char *role,
    const char *text,
    gboolean assistant,
    GtkLabel **stream_label_out,
    GtkWidget **meta_box_out);
```

### Create Tool Trace Revealer

```c
/**
 * Create an expandable tool trace section.
 *
 * Shows collapsible details about tool execution, memory tiers, and judge decisions.
 *
 * @param tool_name         Tool invoked (e.g., "sys/invoke", "math/compute")
 * @param memory_tier       Memory tier used (e.g., "SHORT_TERM", "KNOWLEDGE_BASE")
 * @param judge_decision    Judge decision text (e.g., "APPROVED", "FLAGGED")
 * @return                  GtkExpander widget (add to meta_box with gtk_box_append)
 */
GtkWidget *
create_tool_trace_revealer(
    const char *tool_name,
    const char *memory_tier,
    const char *judge_decision);
```

## Usage Examples

### Basic Message

```c
LiaraWindow *ui = liara_window_new(app, FALSE);
GtkLabel *stream_label = NULL;

/* Add user message */
append_chat_message(ui, "You", "What is 2 + 2?", FALSE, NULL, NULL);

/* Add assistant response (streaming) */
GtkWidget *content_box = append_chat_message(
    ui, "LIARA", "Thinking...", TRUE, &stream_label, NULL
);

/* Update streaming response */
gtk_label_set_text(stream_label, "The answer is 4.");
```

### With Tool Trace

```c
GtkWidget *meta_box = NULL;

/* Create assistant message */
GtkWidget *content_box = append_chat_message(
    ui, "LIARA", "Result: 42", TRUE, NULL, &meta_box
);

/* Add tool trace details */
GtkWidget *trace = create_tool_trace_revealer(
    "math/compute",
    "SHORT_TERM",
    "APPROVED"
);
gtk_box_append(GTK_BOX(meta_box), trace);
gtk_widget_set_visible(meta_box, TRUE);
```

## Dark Theme Colors

### Assistant Bubble (LIARA)
- **Primary**: `rgba(30, 41, 59, 0.6)` - Slate-800 with cyan accent
- **Border**: `rgba(34, 211, 238, 0.25)` - Cyan-400 (30%)
- **Role**: `#67e8f9` - Cyan-300
- **Text**: `#cbd5e1` - Slate-300

### User Bubble
- **Primary**: `rgba(59, 130, 246, 0.18)` - Blue-500 (18%)
- **Border**: `rgba(59, 130, 246, 0.3)` - Blue-500 (30%)
- **Role**: `#60a5fa` - Blue-400
- **Text**: `#e0e8ff` - Blue-50

## Streaming Integration

For long-running responses, use the `stream_label` output parameter:

```c
GtkLabel *stream_label = NULL;
append_chat_message(ui, "LIARA", "Processing...", TRUE, &stream_label, NULL);

/* On each stream chunk */
g_autofree char *updated_text = g_strdup_printf("%s\n%s", current_text, chunk);
gtk_label_set_text(stream_label, updated_text);
```

## Future Extensions

### Simulation Mode
Add `.chat-bubble-simulated` CSS class for dashed borders:

```css
.chat-bubble-simulated {
  border-style: dashed;
  opacity: 0.8;
}
```

### Memory Tier Icons
Extend `create_tool_trace_revealer()` to show memory tier with icons:
- 🧠 SHORT_TERM (volatile)
- 📚 KNOWLEDGE_BASE (long-term)
- 📋 SESSION (current session)

### Copy/Export Buttons
Add action buttons to `chat-bubble-meta`:

```c
GtkWidget *copy_btn = gtk_button_new_from_icon_name("edit-copy-symbolic");
gtk_box_append(GTK_BOX(meta_box), copy_btn);
g_signal_connect(copy_btn, "clicked", G_CALLBACK(on_copy_bubble_clicked), content);
```

## Performance Notes

- ListBox automatically virtualizes rendering for 100+ messages
- Spacer boxes are empty and have minimal overhead
- CSS gradients are GPU-accelerated in modern GTK4
- No forced full-window redraws on streaming updates (only label updates)
