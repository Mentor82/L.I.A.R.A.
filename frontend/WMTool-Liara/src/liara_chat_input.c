#include "liara_chat_input.h"
#include <string.h>

/* Key press handler for Enter/Shift+Enter */
gboolean
liara_chat_input_key_pressed(
    GtkEventControllerKey *controller,
    guint keyval,
    guint keycode,
    GdkModifierType state,
    gpointer user_data)
{
    (void)controller;
    (void)keycode;
    
    LiaraChatInput *input = user_data;
    
    if (input == NULL || input->input_view == NULL) {
        return FALSE;
    }
    
    /* Check if Enter was pressed */
    if (keyval == GDK_KEY_Return || keyval == GDK_KEY_KP_Enter) {
        gboolean shift_pressed = (state & GDK_SHIFT_MASK) != 0;
        
        if (!shift_pressed) {
            /* Plain Enter: send message */
            g_autofree char *text = liara_chat_input_get_text(input->input_view);
            if (text != NULL && text[0] != '\0') {
                gboolean proceed = TRUE;
                
                if (input->send_callback != NULL) {
                    proceed = input->send_callback(text, input->user_data);
                }
                
                if (proceed) {
                    liara_chat_input_clear(input->input_view);
                }
            }
            
            return TRUE;  /* Consume event */
        } else {
            /* Shift+Enter: insert newline */
            GtkTextBuffer *buffer = gtk_text_view_get_buffer(input->input_view);
            gtk_text_buffer_insert_at_cursor(buffer, "\n", -1);
            return TRUE;  /* Consume event */
        }
    }
    
    return FALSE;  /* Let other handlers process */
}

/* Create chat input wrapper */
LiaraChatInput *
liara_chat_input_new(
    GtkTextView *input_view,
    ChatInputCallback send_callback,
    gpointer user_data)
{
    LiaraChatInput *input = g_new0(LiaraChatInput, 1);
    GtkEventController *controller;
    
    input->input_view = input_view;
    input->send_callback = send_callback;
    input->user_data = user_data;
    
    /* Add key event controller */
    controller = gtk_event_controller_key_new();
    g_signal_connect(controller, "key-pressed",
                    G_CALLBACK(liara_chat_input_key_pressed),
                    input);
    gtk_widget_add_controller(GTK_WIDGET(input_view), controller);
    
    input->is_connected = TRUE;
    
    return input;
}

/* Get text from input view */
char *
liara_chat_input_get_text(GtkTextView *input_view)
{
    GtkTextBuffer *buffer;
    GtkTextIter start, end;
    
    if (input_view == NULL) {
        return g_strdup("");
    }
    
    buffer = gtk_text_view_get_buffer(input_view);
    gtk_text_buffer_get_bounds(buffer, &start, &end);
    
    return gtk_text_buffer_get_text(buffer, &start, &end, FALSE);
}

/* Set text in input view */
void
liara_chat_input_set_text(GtkTextView *input_view, const char *text)
{
    GtkTextBuffer *buffer;
    
    if (input_view == NULL) {
        return;
    }
    
    buffer = gtk_text_view_get_buffer(input_view);
    gtk_text_buffer_set_text(buffer, text != NULL ? text : "", -1);
}

/* Clear input view */
void
liara_chat_input_clear(GtkTextView *input_view)
{
    liara_chat_input_set_text(input_view, "");
}

/* Free chat input */
void
liara_chat_input_free(LiaraChatInput *input)
{
    if (input != NULL) {
        g_free(input);
    }
}
