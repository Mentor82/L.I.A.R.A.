#ifndef LIARA_CHAT_INPUT_H
#define LIARA_CHAT_INPUT_H

#include <gtk/gtk.h>

/* Chat input enhancement: Enter sends, Shift+Enter new line */

typedef gboolean (*ChatInputCallback)(const char *message, gpointer user_data);

typedef struct {
    GtkTextView *input_view;
    ChatInputCallback send_callback;
    gpointer user_data;
    gboolean is_connected;
} LiaraChatInput;

/* Setup chat input with Enter/Shift+Enter handling */
LiaraChatInput *liara_chat_input_new(
    GtkTextView *input_view,
    ChatInputCallback send_callback,
    gpointer user_data
);

/* Connect key-press handler */
gboolean liara_chat_input_key_pressed(
    GtkEventControllerKey *controller,
    guint keyval,
    guint keycode,
    GdkModifierType state,
    gpointer user_data
);

/* Free resources */
void liara_chat_input_free(LiaraChatInput *input);

/* Get current text */
char *liara_chat_input_get_text(GtkTextView *input_view);

/* Set text */
void liara_chat_input_set_text(GtkTextView *input_view, const char *text);

/* Clear input */
void liara_chat_input_clear(GtkTextView *input_view);

#endif
