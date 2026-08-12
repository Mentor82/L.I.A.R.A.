#include "liara_formatted_text.h"
#include <string.h>
#include <ctype.h>

/* Copy button handler */
static void
on_copy_code_clicked(GtkButton *button, gpointer user_data)
{
    (void)button;
    const char *code = user_data;
    GdkClipboard *clipboard = gdk_display_get_clipboard(gdk_display_get_default());
    
    if (clipboard != NULL && code != NULL) {
        gdk_clipboard_set_text(clipboard, code);
    }
}

/* Create formatted code block widget */
GtkWidget *
liara_create_code_block(
    const char *code,
    const char *language,
    gboolean show_copy_button)
{
    GtkWidget *container = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    GtkWidget *header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *lang_label = gtk_label_new(NULL);
    GtkWidget *copy_button = NULL;
    GtkWidget *code_view = gtk_text_view_new();
    GtkTextBuffer *buffer;
    
    gtk_widget_add_css_class(container, "code-block");
    gtk_widget_add_css_class(header, "code-block-header");
    gtk_widget_add_css_class(lang_label, "code-block-language");
    gtk_widget_add_css_class(code_view, "code-block-content");
    
    /* Set language label */
    if (language != NULL && language[0] != '\0') {
        g_autofree char *label_text = g_strdup_printf("{ %s }", language);
        gtk_label_set_text(GTK_LABEL(lang_label), label_text);
    } else {
        gtk_label_set_text(GTK_LABEL(lang_label), "{ code }");
    }
    
    /* Configure text view */
    gtk_text_view_set_editable(GTK_TEXT_VIEW(code_view), FALSE);
    gtk_text_view_set_cursor_visible(GTK_TEXT_VIEW(code_view), FALSE);
    gtk_text_view_set_wrap_mode(GTK_TEXT_VIEW(code_view), GTK_WRAP_WORD_CHAR);
    gtk_widget_set_vexpand(code_view, TRUE);
    gtk_widget_set_hexpand(code_view, TRUE);
    
    /* Set monospace font via CSS class */
    gtk_widget_add_css_class(code_view, "code-block-content");
    
    /* Add code text */
    buffer = gtk_text_view_get_buffer(GTK_TEXT_VIEW(code_view));
    gtk_text_buffer_set_text(buffer, code != NULL ? code : "", -1);
    
    /* Build header */
    gtk_box_append(GTK_BOX(header), lang_label);
    gtk_widget_set_hexpand(lang_label, TRUE);
    gtk_widget_set_halign(lang_label, GTK_ALIGN_START);
    
    if (show_copy_button) {
        copy_button = gtk_button_new_with_label("📋");
        gtk_widget_add_css_class(copy_button, "code-block-copy");
        gtk_widget_set_size_request(copy_button, 36, 32);
        gtk_box_append(GTK_BOX(header), copy_button);
        
        g_autofree char *code_copy = g_strdup(code != NULL ? code : "");
        g_signal_connect(copy_button, "clicked", 
                        G_CALLBACK(on_copy_code_clicked), 
                        code_copy);
    }
    
    /* Assemble widget */
    gtk_box_append(GTK_BOX(container), header);
    
    GtkWidget *code_scroller = gtk_scrolled_window_new();
    gtk_widget_set_vexpand(code_scroller, TRUE);
    gtk_widget_set_hexpand(code_scroller, TRUE);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(code_scroller), code_view);
    gtk_box_append(GTK_BOX(container), code_scroller);
    
    return container;
}

/* Extract code language from fence markers */
char *
liara_extract_code_language(const char *text, size_t *code_start)
{
    const char *ptr = text;
    size_t ticks = 0;
    GString *lang = g_string_new("");
    
    /* Count opening backticks */
    while (ptr != NULL && *ptr == '`') {
        ticks++;
        ptr++;
    }
    
    if (ticks < 3) {
        g_string_free(lang, TRUE);
        return NULL;
    }
    
    /* Extract language identifier (alphanumeric after backticks) */
    while (ptr != NULL && *ptr != '\0' && *ptr != '\n' && 
           (isalnum(*ptr) || *ptr == '-' || *ptr == '_')) {
        g_string_append_c(lang, *ptr);
        ptr++;
    }
    
    if (code_start != NULL) {
        *code_start = (ptr - text) + 1;  /* +1 to skip newline */
    }
    
    return g_string_free(lang, FALSE);
}

/* Simple formatted text widget creator */
GtkWidget *
liara_create_formatted_text(const char *text)
{
    GtkWidget *container = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    const char *ptr = text;
    
    if (text == NULL || text[0] == '\0') {
        return container;
    }
    
    /* Simple parser: look for code fences (```language) */
    while (ptr != NULL && *ptr != '\0') {
        if (*ptr == '`' && *(ptr + 1) == '`' && *(ptr + 2) == '`') {
            /* Found code fence - extract language and code */
            const char *fence_start = ptr;
            size_t code_offset = 0;
            g_autofree char *language = liara_extract_code_language(ptr, &code_offset);
            
            ptr += code_offset;
            
            /* Find closing fence */
            const char *code_end = strstr(ptr, "```");
            if (code_end != NULL) {
                g_autofree char *code = g_strndup(ptr, code_end - ptr);
                
                /* Trim leading/trailing newlines from code */
                char *trimmed = code;
                while (*trimmed == '\n') trimmed++;
                size_t code_len = strlen(trimmed);
                while (code_len > 0 && trimmed[code_len - 1] == '\n') {
                    code_len--;
                }
                
                g_autofree char *final_code = g_strndup(trimmed, code_len);
                GtkWidget *code_block = liara_create_code_block(
                    final_code,
                    language,
                    TRUE  /* show copy button */
                );
                gtk_box_append(GTK_BOX(container), code_block);
                
                ptr = code_end + 3;
            } else {
                ptr++;
            }
        } else {
            /* Regular text - collect until next fence or end */
            const char *next_fence = strstr(ptr, "```");
            if (next_fence == NULL) {
                next_fence = ptr + strlen(ptr);
            }
            
            if (next_fence > ptr) {
                g_autofree char *text_segment = g_strndup(ptr, next_fence - ptr);
                
                /* Create text label */
                GtkWidget *label = gtk_label_new(text_segment);
                gtk_label_set_wrap(GTK_LABEL(label), TRUE);
                gtk_label_set_selectable(GTK_LABEL(label), TRUE);
                gtk_widget_add_css_class(label, "chat-text");
                gtk_box_append(GTK_BOX(container), label);
            }
            
            ptr = next_fence;
        }
    }
    
    return container;
}

/* Parse formatted text into segments */
ParsedFormattedText *
liara_parse_formatted_text(const char *text)
{
    ParsedFormattedText *parsed = g_new0(ParsedFormattedText, 1);
    parsed->segments = NULL;
    
    if (text == NULL || text[0] == '\0') {
        return parsed;
    }
    
    const char *ptr = text;
    
    while (ptr != NULL && *ptr != '\0') {
        if (*ptr == '`' && *(ptr + 1) == '`' && *(ptr + 2) == '`') {
            /* Code block */
            size_t code_offset = 0;
            g_autofree char *language = liara_extract_code_language(ptr, &code_offset);
            ptr += code_offset;
            
            const char *code_end = strstr(ptr, "```");
            if (code_end != NULL) {
                FormattedTextSegment *segment = g_new0(FormattedTextSegment, 1);
                segment->is_code = TRUE;
                segment->language = language;
                segment->text = g_strndup(ptr, code_end - ptr);
                parsed->segments = g_slist_append(parsed->segments, segment);
                ptr = code_end + 3;
            } else {
                ptr++;
            }
        } else {
            ptr++;
        }
    }
    
    return parsed;
}

void
liara_parsed_formatted_text_free(ParsedFormattedText *parsed)
{
    if (parsed != NULL) {
        for (GSList *l = parsed->segments; l != NULL; l = l->next) {
            FormattedTextSegment *seg = l->data;
            if (seg != NULL) {
                g_free(seg->text);
                g_free(seg);
            }
        }
        g_slist_free(parsed->segments);
        g_free(parsed);
    }
}
