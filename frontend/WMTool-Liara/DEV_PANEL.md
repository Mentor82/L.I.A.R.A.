# Dev Panel Implementation

## Overview

The dev panel is a **collapsible metadata viewer** integrated into chat bubbles. It shows execution details like:
- `run_id`: Unique execution identifier
- `llm_provider`: Which LLM was used (e.g., "ollama", "openai")
- `llm_model`: Model identifier
- `ttft_ms`: Time to first token (latency)
- `gen_ms`: Generation time
- `trace_steps`: Number of execution steps
- `context_items`: Context chunks used
- `validation`: Validation result
- `judge_passed`: Safety/policy result

## Architecture

### Core Components

```
GtkBox (dev-panel-container)
├── GtkButton (dev-toggle)  "Details ▼"  / "Details ▲"
└── GtkRevealer (hidden/shown with transition)
    └── GtkBox (dev-panel)
        ├── GtkBox (metadata-row)
        │  ├── GtkLabel (dev-meta-key)   "run_id:"
        │  └── GtkLabel (dev-meta-value) "8f3a..."
        ├── GtkBox (metadata-row)
        │  ├── GtkLabel (dev-meta-key)   "llm_model:"
        │  └── GtkLabel (dev-meta-value) "qwen2.5:3b"
        └── ...
```

### Function Signature

```c
/**
 * Create a collapsible dev panel with key-value metadata.
 *
 * @param keys       Array of metadata keys (NULL-terminated)
 * @param values     Array of metadata values (NULL-terminated)
 * @param count      Number of key-value pairs
 * @param is_tool    Set to TRUE for tool-execution styling (blue border)
 * @return           GtkBox containing toggle + revealer (add to chat-bubble or meta-box)
 *
 * Example:
 *   const char *keys[] = {"run_id", "llm_model", "ttft_ms", NULL};
 *   const char *vals[] = {"8f3a...", "qwen2.5:3b", "122", NULL};
 *   GtkWidget *panel = create_dev_panel_with_metadata(keys, vals, 3, FALSE);
 *   gtk_box_append(GTK_BOX(meta_box), panel);
 */
GtkWidget *create_dev_panel_with_metadata(
    const char **keys,
    const char **values,
    gint count,
    gboolean is_tool);
```

## Usage Examples

### Basic Chat Response with Dev Info

```c
/* Create message */
GtkWidget *content_box = NULL;
GtkWidget *meta_box = NULL;
append_chat_message(ui, "LIARA", response_text, TRUE, NULL, &meta_box);

/* Prepare metadata */
const char *dev_keys[] = {
    "run_id",
    "llm_provider",
    "llm_model",
    "ttft_ms",
    "gen_ms",
    NULL
};
const char *dev_vals[] = {
    "8f3a2e10",
    "ollama",
    "qwen2.5:3b",
    "122",
    "456",
    NULL
};

/* Add dev panel */
GtkWidget *dev_panel = create_dev_panel_with_metadata(dev_keys, dev_vals, 5, FALSE);
gtk_box_append(GTK_BOX(meta_box), dev_panel);
gtk_widget_set_visible(meta_box, TRUE);
```

### Tool Execution Bubble

```c
/* Create tool info message */
GtkWidget *tool_content = NULL;
GtkWidget *tool_meta = NULL;
append_chat_message(ui, "🛠 Tool", "Execution in progress...", TRUE, NULL, &tool_meta);

/* Prepare tool metadata */
const char *tool_keys[] = {
    "tool",
    "status",
    "execution_ms",
    "sandbox_root",
    "judge",
    NULL
};
const char *tool_vals[] = {
    "sys/invoke",
    "success",
    "12.3",
    "/home/liara/sessions/abc123",
    "allow",
    NULL
};

/* Add dev panel with tool styling */
GtkWidget *tool_panel = create_dev_panel_with_metadata(tool_keys, tool_vals, 5, TRUE);
gtk_box_append(GTK_BOX(tool_meta), tool_panel);
gtk_widget_set_visible(tool_meta, TRUE);
```

### Dynamic Metadata Update

```c
/* Create panel initially */
const char *keys[] = {"status", "progress", NULL};
const char *vals[] = {"running", "0%", NULL};
GtkWidget *panel = create_dev_panel_with_metadata(keys, vals, 2, FALSE);

/* Later, update values by recreating the panel */
const char *new_vals[] = {"running", "50%", NULL};
// Clear old panel from parent
gtk_box_remove(GTK_BOX(parent), panel);
// Add new panel
panel = create_dev_panel_with_metadata(keys, new_vals, 2, FALSE);
gtk_box_append(GTK_BOX(parent), panel);
```

## CSS Classes

### Structure

```css
.dev-panel-container     /* Outer container with toggle + revealer */
.dev-toggle              /* "Details ▼" button */
.dev-panel               /* Metadata display area */
.dev-panel-tool          /* Tool execution variant (blue border) */
.dev-meta-key            /* Left column: "run_id:" */
.dev-meta-value          /* Right column: "8f3a..." */
```

### Styling Details

**Dev Toggle Button**
- Size: 10px font
- Padding: 2px 6px
- Border: 1px solid (light)
- Background: Slate-900 (20% opacity)
- Hover: Brighter background, higher opacity
- Smooth transition (0.2s)

**Dev Panel**
- Background: Slate-900 (40% opacity)
- Border-left: 2px solid cyan (30%)
- Border-radius: 6px
- Padding: 6px 10px

**Tool Variant** (is_tool=TRUE)
- Background: Gradient (60% → 30% opacity)
- Border-left: 3px solid blue (50%)
- Hover: Border becomes brighter blue

**Metadata Labels**
- Font: Monospace, 10px
- Key: Slate-400, 600 weight, min-width 100px
- Value: Cyan-300, selectable, ellipsize on overflow

## Integration with Chat Flow

### During Streaming

```c
/* 1. Create message with placeholder */
GtkLabel *stream_label = NULL;
GtkWidget *meta_box = NULL;
append_chat_message(ui, "LIARA", "Processing...", TRUE, &stream_label, &meta_box);

/* 2. As chunks arrive, update the label */
gtk_label_set_text(stream_label, accumulated_text);

/* 3. When complete, add dev panel with final metadata */
const char *keys[] = {"run_id", "gen_ms", NULL};
const char *vals[] = {run_id_str, gen_ms_str, NULL};
GtkWidget *panel = create_dev_panel_with_metadata(keys, vals, 2, FALSE);
gtk_box_append(GTK_BOX(meta_box), panel);
gtk_widget_set_visible(meta_box, TRUE);
```

### API Response Handler

```c
/* In your API callback */
static void
on_chat_stream_complete(ChatResponse *response, gpointer user_data)
{
    LiaraWindow *ui = (LiaraWindow *) user_data;
    
    /* Extract metadata from response */
    g_autofree char *run_id_str = g_strdup(response->run_id);
    g_autofree char *provider_str = g_strdup(response->llm_provider);
    g_autofree char *model_str = g_strdup(response->llm_model);
    g_autofree char *ttft_str = g_strdup_printf("%d", response->ttft_ms);
    g_autofree char *gen_str = g_strdup_printf("%d", response->gen_ms);
    
    /* Build dev panel */
    const char *keys[] = {
        "run_id", "llm_provider", "llm_model", "ttft_ms", "gen_ms", NULL
    };
    const char *vals[] = {
        run_id_str, provider_str, model_str, ttft_str, gen_str, NULL
    };
    
    GtkWidget *panel = create_dev_panel_with_metadata(keys, vals, 5, FALSE);
    gtk_box_append(GTK_BOX(ui->current_assistant_meta_box), panel);
    gtk_widget_set_visible(ui->current_assistant_meta_box, TRUE);
}
```

## Performance Notes

- **Revealer Transition**: 200ms slide-down animation (GPU-accelerated)
- **Memory**: Minimal overhead (one GtkBox per metadata row)
- **Rendering**: No full-window redraws; only revealer updates
- **No String Copies**: Values are stored as pointers (ensure they remain valid)

## Customization

### Add Custom Fields

Simply extend the keys/values arrays:

```c
const char *keys[] = {
    "run_id",
    "llm_model",
    "tokens_used",
    "cost_usd",      // Custom field
    "custom_param",  // Custom field
    NULL
};
```

### Change Styling

Edit CSS classes in `style.css`:

```css
.dev-meta-key {
    font-weight: 700;  /* Make keys bold */
}

.dev-panel {
    background: rgba(30, 41, 59, 0.6);  /* Darker background */
}
```

### Disable Tool Styling

Always pass `is_tool=FALSE` for non-tool messages:

```c
GtkWidget *panel = create_dev_panel_with_metadata(keys, vals, count, FALSE);
```

## Future Extensions

### 1. Expandable Nested Details
```c
// For complex metadata trees
.dev-meta-nested {
  margin-left: 20px;
  border-left: 1px dashed;
}
```

### 2. Copy Individual Values
```c
// Add copy buttons per metadata row
GtkWidget *copy_btn = gtk_button_new_from_icon_name("edit-copy-symbolic");
g_signal_connect(copy_btn, "clicked", G_CALLBACK(on_copy_meta_value), value_str);
gtk_box_append(GTK_BOX(row), copy_btn);
```

### 3. Export as JSON
```c
// Serialize dev panel metadata to JSON for external analysis
JsonBuilder *builder = json_builder_new();
for (int i = 0; keys[i] != NULL; i++) {
    json_builder_set_member_name(builder, keys[i]);
    json_builder_add_string_value(builder, vals[i]);
}
```

### 4. Metrics Dashboard
Aggregate and visualize metrics across multiple messages:
- Average TTFT by model
- Total tokens consumed
- Tool execution time distribution
- Validation pass rate
