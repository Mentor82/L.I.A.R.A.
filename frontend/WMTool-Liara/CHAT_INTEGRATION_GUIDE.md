# Chat Bubbles + Dev Panel Integration Guide

## Quick Start

### 1. Display a Simple Chat Message

```c
/* User message (blue, right-aligned) */
append_chat_message(ui, "You", "What is the weather?", FALSE, NULL, NULL);

/* Assistant response (dark/cyan, left-aligned) */
append_chat_message(ui, "LIARA", "I don't have weather data.", TRUE, NULL, NULL);
```

**Visual Output:**
```
                          ┌──────────────────────┐
                          │ You                  │
                          │ What is the weather? │
                          └──────────────────────┘

┌──────────────────────┐
│ LIARA                │
│ I don't have weather │
│ data.                │
└──────────────────────┘
```

---

### 2. Stream Long Responses

```c
GtkLabel *stream_label = NULL;

/* Create assistant message placeholder */
append_chat_message(ui, "LIARA", "Computing...", TRUE, &stream_label, NULL);

/* On each stream chunk from API */
g_autofree char *updated = g_strdup_printf("%s%s", current_text, chunk);
gtk_label_set_text(stream_label, updated);
```

---

### 3. Add Developer Metadata Panel

```c
GtkWidget *content_box = NULL;
GtkWidget *meta_box = NULL;

/* Create message */
append_chat_message(ui, "LIARA", "Result: 42", TRUE, NULL, &meta_box);

/* Prepare metadata */
const char *keys[] = {"run_id", "llm_model", "gen_ms", NULL};
const char *vals[] = {"8f3a2e10", "qwen2.5:3b", "456", NULL};

/* Add dev panel */
GtkWidget *panel = create_dev_panel_with_metadata(keys, vals, 3, FALSE);
gtk_box_append(GTK_BOX(meta_box), panel);
gtk_widget_set_visible(meta_box, TRUE);
```

**Visual Output:**
```
┌──────────────────────────┐
│ LIARA                    │
│ Result: 42               │
│ ┌──────────────────────┐ │
│ │ Details ▼            │ │  <- Click to expand
│ └──────────────────────┘ │
└──────────────────────────┘

After clicking:
┌──────────────────────────┐
│ LIARA                    │
│ Result: 42               │
│ ┌──────────────────────┐ │
│ │ Details ▲            │ │  <- Click to collapse
│ ├──────────────────────┤ │
│ │ run_id:    8f3a2e10  │ │
│ │ llm_model: qwen2.5:3b│ │
│ │ gen_ms:    456       │ │
│ └──────────────────────┘ │
└──────────────────────────┘
```

---

### 4. Tool Execution Panel (Blue Border Variant)

```c
const char *keys[] = {"tool", "status", "execution_ms", "judge", NULL};
const char *vals[] = {"sys/invoke", "success", "12.3", "allow", NULL};

GtkWidget *tool_panel = create_dev_panel_with_metadata(keys, vals, 4, TRUE);
gtk_box_append(GTK_BOX(meta_box), tool_panel);
gtk_widget_set_visible(meta_box, TRUE);
```

**Visual Output (Notice Blue Left Border):**
```
┌──────────────────────────────┐
│ LIARA                        │
│ Tool execution complete      │
│ ┏━━━━━━━━━━━━━━━━━━━━━━━━┓ │
│ ┃ Details ▼              ┃ │  <- Blue border (tool variant)
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━┛ │
└──────────────────────────────┘

After expanding:
┌──────────────────────────────┐
│ LIARA                        │
│ Tool execution complete      │
│ ┏━━━━━━━━━━━━━━━━━━━━━━━━┓ │
│ ┃ Details ▲              ┃ │
│ ┣━━━━━━━━━━━━━━━━━━━━━━━━┫ │
│ ┃ tool:       sys/invoke ┃ │
│ ┃ status:     success    ┃ │
│ ┃ execution_ms: 12.3     ┃ │
│ ┃ judge:      allow      ┃ │
│ ┗━━━━━━━━━━━━━━━━━━━━━━━━┛ │
└──────────────────────────────┘
```

---

## Integration Points

### API Response Handler

```c
/* In your on_chat_complete callback */
static void
on_chat_response(ChatResponse *response, gpointer user_data)
{
    LiaraWindow *ui = (LiaraWindow *) user_data;
    
    /* Update streaming label with final response */
    gtk_label_set_text(ui->current_assistant_label, response->text);
    
    /* Prepare metadata from response */
    g_autofree char *run_id_str = g_strdup(response->run_id);
    g_autofree char *model_str = g_strdup(response->llm_model);
    g_autofree char *ttft_str = g_strdup_printf("%d", response->ttft_ms);
    g_autofree char *gen_str = g_strdup_printf("%d", response->gen_ms);
    
    const char *keys[] = {"run_id", "llm_model", "ttft_ms", "gen_ms", NULL};
    const char *vals[] = {run_id_str, model_str, ttft_str, gen_str, NULL};
    
    /* Add dev panel to metadata box */
    GtkWidget *panel = create_dev_panel_with_metadata(keys, vals, 4, FALSE);
    gtk_box_append(GTK_BOX(ui->current_assistant_meta_box), panel);
    gtk_widget_set_visible(ui->current_assistant_meta_box, TRUE);
}
```

### Tool Invocation Handler

```c
/* When a tool is executed */
static void
on_tool_invoked(ToolResponse *tool_response, gpointer user_data)
{
    LiaraWindow *ui = (LiaraWindow *) user_data;
    
    g_autofree char *exec_ms_str = g_strdup_printf("%.1f", tool_response->execution_ms);
    g_autofree char *judge_str = g_strdup(tool_response->judge_decision);
    
    const char *keys[] = {
        "tool", "status", "execution_ms", "judge", NULL
    };
    const char *vals[] = {
        tool_response->tool_name,
        tool_response->status,
        exec_ms_str,
        judge_str,
        NULL
    };
    
    /* Create tool panel in a separate "message" or within the meta box */
    GtkWidget *tool_panel = create_dev_panel_with_metadata(keys, vals, 4, TRUE);
    gtk_box_append(GTK_BOX(ui->current_assistant_meta_box), tool_panel);
    gtk_widget_set_visible(ui->current_assistant_meta_box, TRUE);
}
```

---

## API Reference

### `append_chat_message()`
```c
GtkWidget *append_chat_message(
    LiaraWindow *ui,
    const char *role,              // "You", "LIARA", etc.
    const char *text,              // Message body
    gboolean assistant,            // TRUE = assistant, FALSE = user
    GtkLabel **stream_label_out,   // Output: label for streaming updates
    GtkWidget **meta_box_out       // Output: box for metadata/panels
);

Returns: Content box (for future formatting updates)
```

### `create_dev_panel_with_metadata()`
```c
GtkWidget *create_dev_panel_with_metadata(
    const char **keys,             // NULL-terminated array of keys
    const char **values,           // Parallel array of values
    gint count,                    // Number of pairs
    gboolean is_tool               // TRUE for tool styling (blue border)
);

Returns: GtkBox (add to meta_box with gtk_box_append)
```

---

## Styling Customization

### Override Default Colors

Edit `style.css` to customize:

```css
/* Chat bubbles */
.chat-bubble-assistant {
  background: linear-gradient(135deg, rgba(30, 41, 59, 0.8) 0%, rgba(15, 23, 42, 0.6) 100%);
}

/* Dev panel */
.dev-panel {
  background: rgba(15, 23, 42, 0.6);  /* Darker */
  border-left-color: rgba(103, 232, 249, 0.5);
}

.dev-toggle {
  color: rgba(226, 232, 240, 0.8);  /* Brighter text */
}
```

### Add Custom CSS Classes

For experiments or custom themes:

```c
/* In append_chat_message() or create_dev_panel_with_metadata() */
gtk_widget_add_css_class(bubble, "custom-theme-dark");

/* Then in style.css */
.custom-theme-dark {
  background: #0a0e27;
  border-color: #1e3a8a;
}
```

---

## Performance Tips

1. **Reuse ListBoxRow Children**
   - Don't recreate panels on every update; update label text instead
   - Only create new panels for truly new metadata

2. **Batch CSS Updates**
   - Add/remove multiple CSS classes in one call
   - GTK batches stylesheet recalculations

3. **Memory Management**
   - Use `g_autofree` for temporary strings
   - Don't keep pointers to const char * arrays that go out of scope

4. **Streaming Optimization**
   - Update `stream_label` via `gtk_label_set_text()` only
   - Avoid recreating the entire content_box per chunk

---

## Testing Checklist

- [ ] User messages appear right-aligned (blue)
- [ ] Assistant messages appear left-aligned (dark/cyan)
- [ ] "Details ▼" button toggles to "Details ▲"
- [ ] Dev panel smoothly expands/collapses (200ms animation)
- [ ] Metadata text is selectable (can copy)
- [ ] Tool panels have blue left border
- [ ] 100+ messages don't cause performance degradation
- [ ] Streaming updates don't cause redraw flicker

---

## Files Reference

- **Implementation**: `src/liara_window.c` (lines 1352+)
- **Styling**: `style.css` and `dist/config/style.css`
- **Full API Docs**: `CHAT_BUBBLES.md`, `DEV_PANEL.md`
- **Refactoring Summary**: `CHAT_BUBBLES_REFACTORING.md`
