#include "liara_window.h"
#include "liara_api.h"

#include <errno.h>
#include <json-glib/json-glib.h>

typedef struct {
    GtkApplication *app;
    GtkWidget *window;
    LiaraApi *api;
    char *config_path;

    GtkStack *main_stack;
    GtkStack *side_stack;

    GtkEntry *session_entry;
    GtkEntry *user_entry;
    GtkTextView *chat_input;
    GtkSpinButton *max_tokens;
    GtkScrolledWindow *chat_transcript_scroller;
    GtkWidget *chat_messages_box;
    GtkWidget *chat_send_button;
    GtkWidget *current_assistant_content;
    GtkLabel *current_assistant_label;
    gboolean current_assistant_started;
    gboolean stream_inflight;
    guint stream_watchdog_source_id;
    gint64 stream_last_event_usec;

    GtkEntry *history_session_entry;
    GtkSpinButton *history_limit;
    GtkCheckButton *history_include_tools;
    GtkScrolledWindow *history_scroller;
    GtkWidget *history_messages_box;

    GtkTextView *status_output;
    GtkEntry *api_base_url_entry;
    GtkEntry *api_host_entry;
    GtkSpinButton *api_port_spin;
    GtkLabel *api_endpoint_hint;
    GtkEntry *session_info_session_entry;
    GtkEntry *session_info_user_entry;
    GtkEntry *session_sandbox_entry;

    GtkTextView *tools_output;
    GtkEntry *tool_name_entry;
    GtkTextView *tool_params_input;
    GtkSpinButton *tool_timeout;
} LiaraWindow;

static void set_text_view_text(GtkTextView *view, const char *text);
static char *get_text_view_text(GtkTextView *view);
static void on_chat_stream_chunk(const char *chunk_text, gpointer user_data);
static void on_chat_stream_heartbeat(const char *heartbeat_payload, gpointer user_data);
static void on_chat_stream_complete(const char *final_payload, GError *error, gpointer user_data);
static void render_message_content(GtkWidget *content_box, const char *text, gboolean assistant);
static void on_history_loaded(const char *response_text, GError *error, gpointer user_data);
static void on_apply_connection_clicked(GtkButton *button, gpointer user_data);
static void on_load_session_clicked(GtkButton *button, gpointer user_data);
static void on_save_session_clicked(GtkButton *button, gpointer user_data);

static void
set_text_view_text(GtkTextView *view, const char *text)
{
    GtkTextBuffer *buffer = gtk_text_view_get_buffer(view);
    gtk_text_buffer_set_text(buffer, text != NULL ? text : "", -1);
}

static char *
get_text_view_text(GtkTextView *view)
{
    GtkTextBuffer *buffer = gtk_text_view_get_buffer(view);
    GtkTextIter start;
    GtkTextIter end;

    gtk_text_buffer_get_bounds(buffer, &start, &end);
    return gtk_text_buffer_get_text(buffer, &start, &end, FALSE);
}

static char *
extract_response_from_chat_payload(const char *payload)
{
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonObject *object;

    if (payload == NULL || !json_parser_load_from_data(parser, payload, -1, NULL)) {
        return g_strdup("");
    }

    object = json_node_get_object(json_parser_get_root(parser));
    if (object == NULL || !json_object_has_member(object, "response")) {
        return g_strdup("");
    }

    return g_strdup(json_object_get_string_member(object, "response"));
}

static char *
build_default_config_path(void)
{
    if (g_path_is_absolute(g_get_prgname())) {
        g_autofree char *exe_dir = g_path_get_dirname(g_get_prgname());
        g_autofree char *dist_dir = g_path_get_dirname(exe_dir);
        return g_build_filename(dist_dir, "config", "gtk-ui.json", NULL);
    }

    {
        g_autofree char *cwd = g_get_current_dir();
        return g_build_filename(cwd, "config", "gtk-ui.json", NULL);
    }
}

static char *
normalize_base_url(const char *raw_url, const char *raw_host, int port)
{
    g_autofree char *url = g_strdup(raw_url != NULL ? raw_url : "");
    g_autofree char *host = g_strdup(raw_host != NULL ? raw_host : "");

    g_strstrip(url);
    g_strstrip(host);

    if (url[0] != '\0') {
        return g_strdup(url);
    }

    if (host[0] == '\0') {
        return g_strdup_printf("http://127.0.0.1:%d", MAX(1, port));
    }

    return g_strdup_printf("http://%s:%d", host, MAX(1, port));
}

static void
sync_endpoint_hint(LiaraWindow *ui, const char *base_url)
{
    g_autofree char *hint = g_strdup_printf("Current endpoint: %s", base_url);
    gtk_label_set_text(ui->api_endpoint_hint, hint);
}

static void
sync_endpoint_controls_from_base_url(LiaraWindow *ui, const char *base_url)
{
    g_autoptr(GUri) uri = NULL;
    const char *host = "127.0.0.1";
    int port = 8010;

    if (base_url != NULL && base_url[0] != '\0') {
        uri = g_uri_parse(base_url, G_URI_FLAGS_NONE, NULL);
        if (uri != NULL) {
            if (g_uri_get_host(uri) != NULL) {
                host = g_uri_get_host(uri);
            }
            if (g_uri_get_port(uri) > 0) {
                port = (int) g_uri_get_port(uri);
            } else if (g_strcmp0(g_uri_get_scheme(uri), "https") == 0) {
                port = 443;
            } else {
                port = 80;
            }
        }
    }

    gtk_editable_set_text(GTK_EDITABLE(ui->api_base_url_entry), base_url != NULL ? base_url : "");
    gtk_editable_set_text(GTK_EDITABLE(ui->api_host_entry), host);
    gtk_spin_button_set_value(ui->api_port_spin, port);
    sync_endpoint_hint(ui, base_url != NULL ? base_url : "http://127.0.0.1:8010");
}

static void
load_connection_config(LiaraWindow *ui)
{
    g_autoptr(JsonParser) parser = json_parser_new();
    g_autoptr(GError) error = NULL;
    JsonObject *root;
    const char *base_url;

    if (ui->config_path == NULL || !g_file_test(ui->config_path, G_FILE_TEST_EXISTS)) {
        sync_endpoint_controls_from_base_url(ui, liara_api_get_base_url(ui->api));
        return;
    }

    if (!json_parser_load_from_file(parser, ui->config_path, &error)) {
        sync_endpoint_controls_from_base_url(ui, liara_api_get_base_url(ui->api));
        return;
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL || !json_object_has_member(root, "api_base_url")) {
        sync_endpoint_controls_from_base_url(ui, liara_api_get_base_url(ui->api));
        return;
    }

    base_url = json_object_get_string_member(root, "api_base_url");
    liara_api_set_base_url(ui->api, base_url);
    sync_endpoint_controls_from_base_url(ui, base_url);
}

static gboolean
save_connection_config(LiaraWindow *ui, const char *base_url, GError **error)
{
    g_autoptr(JsonBuilder) builder = json_builder_new();
    g_autoptr(JsonGenerator) generator = json_generator_new();
    g_autoptr(JsonNode) root = NULL;
    g_autofree char *json_text = NULL;
    g_autofree char *config_dir = NULL;

    config_dir = g_path_get_dirname(ui->config_path);
    if (g_mkdir_with_parents(config_dir, 0755) != 0) {
        g_set_error(error, G_FILE_ERROR, g_file_error_from_errno(errno), "Could not create config directory.");
        return FALSE;
    }

    json_builder_begin_object(builder);
    json_builder_set_member_name(builder, "api_base_url");
    json_builder_add_string_value(builder, base_url);
    json_builder_end_object(builder);

    root = json_builder_get_root(builder);
    json_generator_set_root(generator, root);
    json_text = json_generator_to_data(generator, NULL);
    return g_file_set_contents(ui->config_path, json_text, -1, error);
}

static gboolean
scroll_adjustment_to_bottom(gpointer user_data)
{
    GtkAdjustment *adjustment = GTK_ADJUSTMENT(user_data);
    double value = gtk_adjustment_get_upper(adjustment) - gtk_adjustment_get_page_size(adjustment);

    gtk_adjustment_set_value(adjustment, MAX(0.0, value));
    g_object_unref(adjustment);
    return G_SOURCE_REMOVE;
}

static void
scroll_chat_to_bottom(LiaraWindow *ui)
{
    GtkAdjustment *adjustment = gtk_scrolled_window_get_vadjustment(ui->chat_transcript_scroller);
    g_idle_add(scroll_adjustment_to_bottom, g_object_ref(adjustment));
}

static void
scroll_history_to_bottom(LiaraWindow *ui)
{
    GtkAdjustment *adjustment = gtk_scrolled_window_get_vadjustment(ui->history_scroller);
    g_idle_add(scroll_adjustment_to_bottom, g_object_ref(adjustment));
}

static void
mark_stream_activity(LiaraWindow *ui)
{
    ui->stream_last_event_usec = g_get_monotonic_time();
}

static void
clear_box_children(GtkWidget *widget)
{
    GtkWidget *child = gtk_widget_get_first_child(widget);

    while (child != NULL) {
        GtkWidget *next = gtk_widget_get_next_sibling(child);
        gtk_box_remove(GTK_BOX(widget), child);
        child = next;
    }
}

static char *
build_inline_markup(const char *text, gboolean assistant)
{
    GString *markup = g_string_new("");
    gboolean in_code = FALSE;
    const char *cursor = text;

    while (*cursor != '\0') {
        const char *tick = strchr(cursor, '`');
        g_autofree char *escaped = NULL;

        if (tick == NULL) {
            escaped = g_markup_escape_text(cursor, -1);
            g_string_append(markup, escaped);
            break;
        }

        escaped = g_markup_escape_text(cursor, tick - cursor);
        g_string_append(markup, escaped);
        in_code = !in_code;
        if (in_code) {
            g_string_append(
                markup,
                assistant
                    ? "<span font_family=\"Cascadia Code, Consolas, monospace\" foreground=\"#0ea5b7\">"
                    : "<span font_family=\"Cascadia Code, Consolas, monospace\" foreground=\"#dbeafe\">"
            );
        } else {
            g_string_append(markup, "</span>");
        }
        cursor = tick + 1;
    }

    if (in_code) {
        g_string_append(markup, "</span>");
    }

    return g_string_free(markup, FALSE);
}

static GtkWidget *
create_markdown_label(const char *text, const char *css_class, gboolean assistant)
{
    GtkWidget *label = gtk_label_new(NULL);
    g_autofree char *markup = build_inline_markup(text, assistant);

    gtk_widget_add_css_class(label, css_class);
    gtk_label_set_xalign(GTK_LABEL(label), 0.0f);
    gtk_label_set_wrap(GTK_LABEL(label), TRUE);
    gtk_label_set_wrap_mode(GTK_LABEL(label), PANGO_WRAP_WORD_CHAR);
    gtk_label_set_selectable(GTK_LABEL(label), TRUE);
    gtk_label_set_use_markup(GTK_LABEL(label), TRUE);
    gtk_label_set_markup(GTK_LABEL(label), markup);
    return label;
}

static GtkWidget *
create_code_block(const char *language, const char *code_text)
{
    GtkWidget *wrapper = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    GtkWidget *header = NULL;
    GtkWidget *scroller = gtk_scrolled_window_new();
    GtkWidget *view = gtk_text_view_new();
    GtkTextBuffer *buffer = gtk_text_view_get_buffer(GTK_TEXT_VIEW(view));

    gtk_widget_add_css_class(wrapper, "code-block-wrap");
    gtk_widget_add_css_class(scroller, "code-block");

    if (language != NULL && language[0] != '\0') {
        header = gtk_label_new(language);
        gtk_widget_add_css_class(header, "code-language");
        gtk_label_set_xalign(GTK_LABEL(header), 0.0f);
        gtk_box_append(GTK_BOX(wrapper), header);
        gtk_widget_add_css_class(scroller, language);
    }

    gtk_text_view_set_editable(GTK_TEXT_VIEW(view), FALSE);
    gtk_text_view_set_cursor_visible(GTK_TEXT_VIEW(view), FALSE);
    gtk_text_view_set_monospace(GTK_TEXT_VIEW(view), TRUE);
    gtk_text_view_set_wrap_mode(GTK_TEXT_VIEW(view), GTK_WRAP_NONE);
    gtk_text_buffer_set_text(buffer, code_text != NULL ? code_text : "", -1);
    gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(scroller), GTK_POLICY_AUTOMATIC, GTK_POLICY_NEVER);
    gtk_scrolled_window_set_min_content_height(GTK_SCROLLED_WINDOW(scroller), 56);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scroller), view);

    gtk_box_append(GTK_BOX(wrapper), scroller);
    return wrapper;
}

static GtkWidget *
create_blockquote(const char *text, gboolean assistant)
{
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    GtkWidget *label = create_markdown_label(text, "md-blockquote", assistant);

    gtk_widget_add_css_class(box, "blockquote-box");
    gtk_box_append(GTK_BOX(box), label);
    return box;
}

static void
append_markdown_line(GtkWidget *content_box, const char *line, gboolean assistant)
{
    GtkWidget *label = NULL;

    if (g_str_has_prefix(line, "### ")) {
        label = create_markdown_label(line + 4, "md-heading-3", assistant);
    } else if (g_str_has_prefix(line, "## ")) {
        label = create_markdown_label(line + 3, "md-heading-2", assistant);
    } else if (g_str_has_prefix(line, "# ")) {
        label = create_markdown_label(line + 2, "md-heading-1", assistant);
    } else if (g_str_has_prefix(line, "- ") || g_str_has_prefix(line, "* ")) {
        g_autofree char *bullet = g_strdup_printf("• %s", line + 2);
        label = create_markdown_label(bullet, "md-bullet", assistant);
    } else {
        label = create_markdown_label(line, "message-text", assistant);
    }

    gtk_box_append(GTK_BOX(content_box), label);
}

static void
render_plain_markdown(GtkWidget *content_box, const char *text, gboolean assistant)
{
    g_auto(GStrv) lines = NULL;
    guint i = 0;

    lines = g_strsplit(text != NULL ? text : "", "\n", -1);
    while (lines[i] != NULL) {
        if (lines[i][0] == '|' ) {
            GString *table = g_string_new("");

            while (lines[i] != NULL && lines[i][0] == '|') {
                g_string_append(table, lines[i]);
                if (lines[i + 1] != NULL && lines[i + 1][0] == '|') {
                    g_string_append_c(table, '\n');
                }
                i++;
            }

            gtk_box_append(GTK_BOX(content_box), create_code_block("table", table->str));
            g_string_free(table, TRUE);
            continue;
        }

        if (lines[i][0] == '\0') {
            GtkWidget *spacer = gtk_separator_new(GTK_ORIENTATION_HORIZONTAL);
            gtk_widget_add_css_class(spacer, "md-spacer");
            gtk_box_append(GTK_BOX(content_box), spacer);
            i++;
            continue;
        }

        if (g_str_has_prefix(lines[i], "> ")) {
            gtk_box_append(GTK_BOX(content_box), create_blockquote(lines[i] + 2, assistant));
            i++;
            continue;
        }

        append_markdown_line(content_box, lines[i], assistant);
        i++;
    }
}

static void
render_message_content(GtkWidget *content_box, const char *text, gboolean assistant)
{
    g_auto(GStrv) lines = NULL;
    GString *plain = g_string_new("");
    GString *code = NULL;
    char *language = NULL;
    gboolean in_code = FALSE;
    guint i;

    clear_box_children(content_box);
    lines = g_strsplit(text != NULL ? text : "", "\n", -1);

    for (i = 0; lines[i] != NULL; i++) {
        if (g_str_has_prefix(lines[i], "```")) {
            if (in_code) {
                gtk_box_append(GTK_BOX(content_box), create_code_block(language, code != NULL ? code->str : ""));
                if (code != NULL) {
                    g_string_free(code, TRUE);
                    code = NULL;
                }
                g_clear_pointer(&language, g_free);
                in_code = FALSE;
            } else {
                if (plain->len > 0) {
                    render_plain_markdown(content_box, plain->str, assistant);
                    g_string_set_size(plain, 0);
                }
                language = g_strdup(lines[i] + 3);
                g_strstrip(language);
                code = g_string_new("");
                in_code = TRUE;
            }
            continue;
        }

        if (in_code) {
            g_string_append(code, lines[i]);
            if (lines[i + 1] != NULL) {
                g_string_append_c(code, '\n');
            }
            continue;
        }

        g_string_append(plain, lines[i]);
        if (lines[i + 1] != NULL) {
            g_string_append_c(plain, '\n');
        }
    }

    if (in_code) {
        gtk_box_append(GTK_BOX(content_box), create_code_block(language, code != NULL ? code->str : ""));
    } else if (plain->len > 0) {
        render_plain_markdown(content_box, plain->str, assistant);
    }

    if (code != NULL) {
        g_string_free(code, TRUE);
        code = NULL;
    }
    g_clear_pointer(&language, g_free);
    g_string_free(plain, TRUE);
}

static GtkWidget *
append_chat_message(LiaraWindow *ui, const char *role, const char *text, gboolean assistant, GtkLabel **stream_label_out)
{
    GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    GtkWidget *bubble = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    GtkWidget *role_label = gtk_label_new(role);
    GtkWidget *content_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget *content_label = gtk_label_new(text);

    gtk_widget_add_css_class(row, "message-row");
    gtk_widget_add_css_class(bubble, "message-bubble");
    gtk_widget_add_css_class(bubble, assistant ? "assistant-bubble" : "user-bubble");
    gtk_widget_add_css_class(role_label, "message-role");
    gtk_widget_add_css_class(content_box, "message-content");
    gtk_widget_add_css_class(content_label, "message-text");

    gtk_widget_set_halign(row, assistant ? GTK_ALIGN_START : GTK_ALIGN_END);
    gtk_widget_set_hexpand(row, TRUE);

    gtk_label_set_xalign(GTK_LABEL(role_label), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(content_label), 0.0f);
    gtk_label_set_wrap(GTK_LABEL(content_label), TRUE);
    gtk_label_set_wrap_mode(GTK_LABEL(content_label), PANGO_WRAP_WORD_CHAR);
    gtk_label_set_selectable(GTK_LABEL(content_label), TRUE);
    gtk_label_set_max_width_chars(GTK_LABEL(content_label), 72);

    gtk_box_append(GTK_BOX(bubble), role_label);
    gtk_box_append(GTK_BOX(content_box), content_label);
    gtk_box_append(GTK_BOX(bubble), content_box);
    gtk_box_append(GTK_BOX(row), bubble);
    gtk_box_append(GTK_BOX(ui->chat_messages_box), row);

    if (stream_label_out != NULL) {
        *stream_label_out = GTK_LABEL(content_label);
    }

    if (!assistant) {
        render_message_content(content_box, text, FALSE);
    }

    scroll_chat_to_bottom(ui);
    return content_box;
}

static void
api_response_to_view(const char *response_text, GError *error, gpointer user_data)
{
    GtkTextView *view = GTK_TEXT_VIEW(user_data);

    if (error != NULL) {
        g_autofree char *message = g_strdup_printf("Request failed\n\n%s", error->message);
        set_text_view_text(view, message);
        g_error_free(error);
        return;
    }

    set_text_view_text(view, response_text);
}

static GtkWidget *
append_history_message(LiaraWindow *ui, const char *role, const char *text)
{
    GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    GtkWidget *bubble = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    GtkWidget *role_label = gtk_label_new(role);
    GtkWidget *content_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    gboolean assistant = g_strcmp0(role, "assistant") == 0 || g_strcmp0(role, "tool") == 0;

    gtk_widget_add_css_class(row, "message-row");
    gtk_widget_add_css_class(bubble, "message-bubble");
    gtk_widget_add_css_class(bubble, assistant ? "assistant-bubble" : "user-bubble");
    gtk_widget_add_css_class(role_label, "message-role");
    gtk_widget_add_css_class(content_box, "message-content");

    gtk_widget_set_halign(row, assistant ? GTK_ALIGN_START : GTK_ALIGN_END);
    gtk_widget_set_hexpand(row, TRUE);
    gtk_label_set_xalign(GTK_LABEL(role_label), 0.0f);

    gtk_box_append(GTK_BOX(bubble), role_label);
    gtk_box_append(GTK_BOX(bubble), content_box);
    gtk_box_append(GTK_BOX(row), bubble);
    gtk_box_append(GTK_BOX(ui->history_messages_box), row);

    render_message_content(content_box, text != NULL ? text : "", assistant);
    scroll_history_to_bottom(ui);
    return content_box;
}

static void
reset_stream_ui(LiaraWindow *ui, const char *status_message)
{
    ui->stream_inflight = FALSE;
    ui->current_assistant_content = NULL;
    ui->current_assistant_label = NULL;
    ui->current_assistant_started = FALSE;

    if (ui->stream_watchdog_source_id != 0) {
        g_source_remove(ui->stream_watchdog_source_id);
        ui->stream_watchdog_source_id = 0;
    }

    gtk_widget_set_sensitive(ui->chat_send_button, TRUE);
    gtk_button_set_label(GTK_BUTTON(ui->chat_send_button), "Send");

    if (status_message != NULL) {
        set_text_view_text(ui->status_output, status_message);
    }
}

static gboolean
on_stream_watchdog_tick(gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const gint64 now = g_get_monotonic_time();
    const gint64 timeout_usec = 15 * G_USEC_PER_SEC;

    if (!ui->stream_inflight) {
        ui->stream_watchdog_source_id = 0;
        return G_SOURCE_REMOVE;
    }

    if ((now - ui->stream_last_event_usec) <= timeout_usec) {
        return G_SOURCE_CONTINUE;
    }

    if (ui->current_assistant_label != NULL) {
        gtk_label_set_text(
            ui->current_assistant_label,
            "Stream connection timed out. The assistant stopped receiving updates."
        );
    } else {
        append_chat_message(
            ui,
            "LIARA",
            "Stream connection timed out. The assistant stopped receiving updates.",
            TRUE,
            NULL
        );
    }

    reset_stream_ui(
        ui,
        "Watchdog timeout: no stream event or heartbeat received for 15 seconds. The UI was reset."
    );
    return G_SOURCE_REMOVE;
}

static void
on_history_loaded(const char *response_text, GError *error, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonObject *root;
    JsonArray *items;
    guint i;

    clear_box_children(ui->history_messages_box);

    if (error != NULL) {
        append_history_message(ui, "assistant", error->message);
        g_error_free(error);
        return;
    }

    if (response_text == NULL || !json_parser_load_from_data(parser, response_text, -1, NULL)) {
        append_history_message(ui, "assistant", "History response could not be parsed.");
        return;
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL || !json_object_has_member(root, "items")) {
        append_history_message(ui, "assistant", "History response did not contain items.");
        return;
    }

    items = json_object_get_array_member(root, "items");
    if (items == NULL || json_array_get_length(items) == 0) {
        append_history_message(ui, "assistant", "No history available for this session yet.");
        return;
    }

    for (i = 0; i < json_array_get_length(items); i++) {
        JsonObject *item = json_array_get_object_element(items, i);
        const char *role = json_object_has_member(item, "role") ? json_object_get_string_member(item, "role") : "assistant";
        const char *content = json_object_has_member(item, "content") ? json_object_get_string_member(item, "content") : "";

        append_history_message(ui, role, content);
    }
}

static void
on_chat_stream_chunk(const char *chunk_text, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *existing_text;
    g_autofree char *updated_text = NULL;

    if (!ui->stream_inflight || ui->current_assistant_label == NULL || chunk_text == NULL || chunk_text[0] == '\0') {
        return;
    }

    mark_stream_activity(ui);

    if (!ui->current_assistant_started) {
        gtk_label_set_text(ui->current_assistant_label, chunk_text);
        ui->current_assistant_started = TRUE;
        scroll_chat_to_bottom(ui);
        return;
    }

    existing_text = gtk_label_get_text(ui->current_assistant_label);
    updated_text = g_strconcat(existing_text, chunk_text, NULL);
    gtk_label_set_text(ui->current_assistant_label, updated_text);
    scroll_chat_to_bottom(ui);
}

static void
on_chat_stream_heartbeat(const char *heartbeat_payload, gpointer user_data)
{
    LiaraWindow *ui = user_data;

    if (!ui->stream_inflight) {
        return;
    }

    (void) heartbeat_payload;
    mark_stream_activity(ui);
}

static void
on_chat_stream_complete(const char *final_payload, GError *error, gpointer user_data)
{
    LiaraWindow *ui = user_data;

    if (!ui->stream_inflight && ui->current_assistant_label == NULL && ui->current_assistant_content == NULL) {
        return;
    }

    mark_stream_activity(ui);

    if (error != NULL) {
        g_autofree char *message = g_strdup_printf("Stream failed\n\n%s", error->message);
        if (ui->current_assistant_label != NULL) {
            gtk_label_set_text(ui->current_assistant_label, message);
        } else {
            append_chat_message(ui, "LIARA", message, TRUE, NULL);
        }
        set_text_view_text(ui->status_output, message);
    } else {
        g_autofree char *response_text = extract_response_from_chat_payload(final_payload);

        if (final_payload != NULL) {
            set_text_view_text(ui->status_output, final_payload);
        }

        if (ui->current_assistant_content != NULL && response_text[0] != '\0') {
            render_message_content(ui->current_assistant_content, response_text, TRUE);
        } else if (ui->current_assistant_label != NULL && !ui->current_assistant_started && response_text[0] != '\0') {
            gtk_label_set_text(ui->current_assistant_label, response_text);
        }
    }

    reset_stream_ui(ui, NULL);
    scroll_chat_to_bottom(ui);
}

static void
switch_view(LiaraWindow *ui, const char *main_name, const char *side_name)
{
    gtk_stack_set_visible_child_name(ui->main_stack, main_name);
    gtk_stack_set_visible_child_name(ui->side_stack, side_name);
}

static void
on_nav_chat_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    switch_view(ui, "chat", "status");
}

static void
on_nav_history_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    switch_view(ui, "history", "history-side");
}

static void
on_nav_tools_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    switch_view(ui, "tools", "tools");
}

static void
on_nav_status_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    switch_view(ui, "chat", "status");
}

static void
on_send_chat_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    g_autofree char *message = get_text_view_text(ui->chat_input);
    char *trimmed_message = message;
    const char *session_id = gtk_editable_get_text(GTK_EDITABLE(ui->session_entry));
    const char *user_id = gtk_editable_get_text(GTK_EDITABLE(ui->user_entry));
    int max_tokens = gtk_spin_button_get_value_as_int(ui->max_tokens);

    (void) button;

    trimmed_message = g_strstrip(trimmed_message);
    if (ui->stream_inflight || trimmed_message[0] == '\0') {
        return;
    }

    gtk_editable_set_text(GTK_EDITABLE(ui->session_info_session_entry), session_id);
    gtk_editable_set_text(GTK_EDITABLE(ui->session_info_user_entry), user_id);
    append_chat_message(ui, "You", trimmed_message, FALSE, NULL);
    ui->current_assistant_content = append_chat_message(ui, "LIARA", "Thinking ...", TRUE, &ui->current_assistant_label);
    ui->current_assistant_started = FALSE;
    ui->stream_inflight = TRUE;
    mark_stream_activity(ui);
    if (ui->stream_watchdog_source_id != 0) {
        g_source_remove(ui->stream_watchdog_source_id);
    }
    ui->stream_watchdog_source_id = g_timeout_add_seconds(1, on_stream_watchdog_tick, ui);
    gtk_widget_set_sensitive(ui->chat_send_button, FALSE);
    gtk_button_set_label(GTK_BUTTON(ui->chat_send_button), "Streaming...");
    set_text_view_text(ui->chat_input, "");
    switch_view(ui, "chat", "status");

    liara_api_post_chat_stream(
        ui->api,
        session_id,
        user_id,
        trimmed_message,
        max_tokens,
        on_chat_stream_chunk,
        on_chat_stream_heartbeat,
        on_chat_stream_complete,
        ui
    );
}

static void
on_refresh_history_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *session_id = gtk_editable_get_text(GTK_EDITABLE(ui->history_session_entry));
    int limit = gtk_spin_button_get_value_as_int(ui->history_limit);
    gboolean include_tools = gtk_check_button_get_active(ui->history_include_tools);

    (void) button;

    liara_api_get_history(
        ui->api,
        session_id,
        limit,
        include_tools,
        on_history_loaded,
        ui
    );
}

static void
on_apply_connection_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    g_autofree char *base_url = NULL;
    g_autoptr(GError) error = NULL;
    const char *raw_url = gtk_editable_get_text(GTK_EDITABLE(ui->api_base_url_entry));
    const char *raw_host = gtk_editable_get_text(GTK_EDITABLE(ui->api_host_entry));
    int port = gtk_spin_button_get_value_as_int(ui->api_port_spin);

    (void) button;

    base_url = normalize_base_url(raw_url, raw_host, port);
    liara_api_set_base_url(ui->api, base_url);
    sync_endpoint_controls_from_base_url(ui, base_url);

    if (!save_connection_config(ui, base_url, &error)) {
        g_autofree char *message = g_strdup_printf(
            "Connection updated to %s\n\nConfig save failed: %s",
            base_url,
            error != NULL ? error->message : "unknown error"
        );
        set_text_view_text(ui->status_output, message);
        return;
    }

    {
        g_autofree char *message = g_strdup_printf(
            "Connection settings applied.\n\nBase URL: %s\nConfig: %s",
            base_url,
            ui->config_path
        );
        set_text_view_text(ui->status_output, message);
    }
}

static void
on_load_session_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *session_id = gtk_editable_get_text(GTK_EDITABLE(ui->session_info_session_entry));
    const char *user_id = gtk_editable_get_text(GTK_EDITABLE(ui->session_info_user_entry));

    (void) button;
    gtk_editable_set_text(GTK_EDITABLE(ui->session_entry), session_id);
    gtk_editable_set_text(GTK_EDITABLE(ui->user_entry), user_id);
    liara_api_get_session(ui->api, session_id, user_id, api_response_to_view, ui->status_output);
}

static void
on_save_session_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *session_id = gtk_editable_get_text(GTK_EDITABLE(ui->session_info_session_entry));
    const char *user_id = gtk_editable_get_text(GTK_EDITABLE(ui->session_info_user_entry));
    const char *sandbox_root = gtk_editable_get_text(GTK_EDITABLE(ui->session_sandbox_entry));

    (void) button;
    gtk_editable_set_text(GTK_EDITABLE(ui->session_entry), session_id);
    gtk_editable_set_text(GTK_EDITABLE(ui->user_entry), user_id);
    liara_api_post_session(
        ui->api,
        session_id,
        user_id,
        sandbox_root,
        api_response_to_view,
        ui->status_output
    );
}

static void
on_health_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    liara_api_get_health(ui->api, api_response_to_view, ui->status_output);
}

static void
on_health_backends_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    liara_api_get_health_backends(ui->api, api_response_to_view, ui->status_output);
}

static void
on_list_tools_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    liara_api_get_tools(ui->api, api_response_to_view, ui->tools_output);
}

static void
on_invoke_tool_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    g_autofree char *parameters = get_text_view_text(ui->tool_params_input);
    const char *tool_name = gtk_editable_get_text(GTK_EDITABLE(ui->tool_name_entry));
    int timeout_seconds = gtk_spin_button_get_value_as_int(ui->tool_timeout);

    (void) button;

    liara_api_post_tool_invoke(
        ui->api,
        tool_name,
        parameters,
        timeout_seconds,
        api_response_to_view,
        ui->tools_output
    );
}

static GtkWidget *
make_panel_title(const char *eyebrow, const char *title, const char *subtitle)
{
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    GtkWidget *eyebrow_label = gtk_label_new(eyebrow);
    GtkWidget *title_label = gtk_label_new(title);
    GtkWidget *subtitle_label = gtk_label_new(subtitle);

    gtk_widget_add_css_class(eyebrow_label, "eyebrow");
    gtk_widget_add_css_class(title_label, "panel-title");
    gtk_widget_add_css_class(subtitle_label, "panel-subtitle");
    gtk_label_set_xalign(GTK_LABEL(eyebrow_label), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(title_label), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(subtitle_label), 0.0f);

    gtk_box_append(GTK_BOX(box), eyebrow_label);
    gtk_box_append(GTK_BOX(box), title_label);
    if (subtitle != NULL) {
        gtk_box_append(GTK_BOX(box), subtitle_label);
    }
    return box;
}

static GtkWidget *
make_editor_view(GtkTextView **out_view, const char *css_class, gboolean editable)
{
    GtkWidget *scrolled = gtk_scrolled_window_new();
    GtkWidget *view = gtk_text_view_new();

    gtk_widget_add_css_class(scrolled, css_class);
    gtk_text_view_set_wrap_mode(GTK_TEXT_VIEW(view), GTK_WRAP_WORD_CHAR);
    gtk_text_view_set_monospace(GTK_TEXT_VIEW(view), TRUE);
    gtk_text_view_set_editable(GTK_TEXT_VIEW(view), editable);
    gtk_widget_set_vexpand(scrolled, TRUE);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scrolled), view);
    *out_view = GTK_TEXT_VIEW(view);
    return scrolled;
}

static GtkWidget *
make_sidebar_button(const char *title, const char *subtitle, GCallback callback, LiaraWindow *ui)
{
    GtkWidget *button = gtk_button_new();
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    GtkWidget *title_label = gtk_label_new(title);
    GtkWidget *subtitle_label = gtk_label_new(subtitle);

    gtk_widget_add_css_class(button, "nav-button");
    gtk_widget_add_css_class(title_label, "nav-title");
    gtk_widget_add_css_class(subtitle_label, "nav-subtitle");
    gtk_label_set_xalign(GTK_LABEL(title_label), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(subtitle_label), 0.0f);
    gtk_box_append(GTK_BOX(box), title_label);
    gtk_box_append(GTK_BOX(box), subtitle_label);
    gtk_button_set_child(GTK_BUTTON(button), box);
    g_signal_connect(button, "clicked", callback, ui);
    return button;
}

static GtkWidget *
build_sidebar(LiaraWindow *ui)
{
    GtkWidget *sidebar = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    GtkWidget *brand_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);

    gtk_widget_add_css_class(sidebar, "sidebar");
    gtk_widget_add_css_class(brand_card, "brand-card");

    gtk_box_append(GTK_BOX(brand_card), make_panel_title("assistant", "LIARA", "Local intelligence with service boundaries"));
    gtk_box_append(GTK_BOX(sidebar), brand_card);
    gtk_box_append(GTK_BOX(sidebar), make_sidebar_button("Chat", "Conversation and response flow", G_CALLBACK(on_nav_chat_clicked), ui));
    gtk_box_append(GTK_BOX(sidebar), make_sidebar_button("History", "Session transcript and memory recall", G_CALLBACK(on_nav_history_clicked), ui));
    gtk_box_append(GTK_BOX(sidebar), make_sidebar_button("Tools", "Discover and invoke builtins", G_CALLBACK(on_nav_tools_clicked), ui));
    gtk_box_append(GTK_BOX(sidebar), make_sidebar_button("Status", "API and memory health", G_CALLBACK(on_nav_status_clicked), ui));

    return sidebar;
}

static GtkWidget *
build_chat_view(LiaraWindow *ui)
{
    GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    GtkWidget *hero = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget *chips = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *transcript_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    GtkWidget *transcript_scroller = gtk_scrolled_window_new();
    GtkWidget *messages_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    GtkWidget *composer_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    GtkWidget *controls = gtk_grid_new();
    GtkWidget *send_button = gtk_button_new_with_label("Send");
    GtkWidget *input_view = make_editor_view(&ui->chat_input, "composer-input", TRUE);

    ui->session_entry = GTK_ENTRY(gtk_entry_new());
    ui->user_entry = GTK_ENTRY(gtk_entry_new());
    ui->max_tokens = GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1, 8192, 1));
    ui->chat_transcript_scroller = GTK_SCROLLED_WINDOW(transcript_scroller);
    ui->chat_messages_box = messages_box;
    ui->chat_send_button = send_button;

    gtk_editable_set_text(GTK_EDITABLE(ui->session_entry), "session-desktop");
    gtk_editable_set_text(GTK_EDITABLE(ui->user_entry), "user-desktop");
    gtk_spin_button_set_value(ui->max_tokens, 2048);

    gtk_widget_add_css_class(page, "content-page");
    gtk_widget_add_css_class(hero, "hero");
    gtk_widget_add_css_class(transcript_card, "surface-card");
    gtk_widget_add_css_class(transcript_scroller, "chat-transcript");
    gtk_widget_add_css_class(messages_box, "chat-messages");
    gtk_widget_add_css_class(composer_card, "surface-card");
    gtk_widget_add_css_class(composer_card, "composer-card");
    gtk_widget_add_css_class(send_button, "suggested-action");
    gtk_grid_set_row_spacing(GTK_GRID(controls), 6);
    gtk_grid_set_column_spacing(GTK_GRID(controls), 8);
    gtk_widget_set_vexpand(transcript_scroller, TRUE);
    gtk_widget_set_vexpand(transcript_card, TRUE);
    gtk_widget_set_valign(messages_box, GTK_ALIGN_START);
    gtk_widget_set_vexpand(input_view, FALSE);
    gtk_widget_set_size_request(input_view, -1, 150);

    gtk_box_append(GTK_BOX(hero), make_panel_title("workspace", "Chat", "ChatGPT-like flow with a native GTK shell"));
    gtk_box_append(GTK_BOX(chips), gtk_label_new("local api"));
    gtk_box_append(GTK_BOX(chips), gtk_label_new("github-style text"));
    gtk_box_append(GTK_BOX(chips), gtk_label_new("cortana accent"));
    gtk_widget_add_css_class(chips, "chip-row");
    gtk_box_append(GTK_BOX(hero), chips);

    gtk_grid_attach(GTK_GRID(controls), gtk_label_new("Session"), 0, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(controls), GTK_WIDGET(ui->session_entry), 1, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(controls), gtk_label_new("User"), 2, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(controls), GTK_WIDGET(ui->user_entry), 3, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(controls), gtk_label_new("Max tokens"), 4, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(controls), GTK_WIDGET(ui->max_tokens), 5, 0, 1, 1);

    gtk_box_append(GTK_BOX(composer_card), make_panel_title("compose", "Prompt", NULL));
    gtk_box_append(GTK_BOX(composer_card), controls);
    gtk_box_append(GTK_BOX(composer_card), input_view);
    gtk_box_append(GTK_BOX(composer_card), send_button);

    gtk_box_append(GTK_BOX(transcript_card), make_panel_title("assistant", "Transcript", "Streaming chat bubbles over the live /chat/stream endpoint"));
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(transcript_scroller), messages_box);
    gtk_box_append(GTK_BOX(transcript_card), transcript_scroller);

    gtk_box_append(GTK_BOX(page), hero);
    gtk_box_append(GTK_BOX(page), transcript_card);
    gtk_box_append(GTK_BOX(page), composer_card);

    append_chat_message(ui, "LIARA", "Native GTK shell ready. Responses will stream here in real time.", TRUE, NULL);

    g_signal_connect(send_button, "clicked", G_CALLBACK(on_send_chat_clicked), ui);
    return page;
}

static GtkWidget *
build_history_view(LiaraWindow *ui)
{
    GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    GtkWidget *card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    GtkWidget *grid = gtk_grid_new();
    GtkWidget *refresh_button = gtk_button_new_with_label("Refresh History");
    GtkWidget *history_scroller = gtk_scrolled_window_new();
    GtkWidget *history_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);

    ui->history_session_entry = GTK_ENTRY(gtk_entry_new());
    ui->history_limit = GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1, 500, 1));
    ui->history_include_tools = GTK_CHECK_BUTTON(gtk_check_button_new_with_label("Include tool messages"));
    ui->history_scroller = GTK_SCROLLED_WINDOW(history_scroller);
    ui->history_messages_box = history_box;

    gtk_editable_set_text(GTK_EDITABLE(ui->history_session_entry), "session-desktop");
    gtk_spin_button_set_value(ui->history_limit, 50);
    gtk_check_button_set_active(ui->history_include_tools, TRUE);

    gtk_widget_add_css_class(page, "content-page");
    gtk_widget_add_css_class(card, "surface-card");
    gtk_widget_add_css_class(history_scroller, "chat-transcript");
    gtk_widget_add_css_class(history_box, "chat-messages");
    gtk_grid_set_row_spacing(GTK_GRID(grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(grid), 8);
    gtk_widget_set_vexpand(history_scroller, TRUE);
    gtk_widget_set_vexpand(card, TRUE);
    gtk_widget_set_valign(history_box, GTK_ALIGN_START);

    gtk_grid_attach(GTK_GRID(grid), gtk_label_new("Session"), 0, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->history_session_entry), 1, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), gtk_label_new("Limit"), 2, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->history_limit), 3, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->history_include_tools), 0, 1, 4, 1);

    gtk_box_append(GTK_BOX(card), make_panel_title("memory", "History", "Readable transcript output with GitHub-like monospace detail"));
    gtk_box_append(GTK_BOX(card), grid);
    gtk_box_append(GTK_BOX(card), refresh_button);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(history_scroller), history_box);
    gtk_box_append(GTK_BOX(card), history_scroller);
    gtk_box_append(GTK_BOX(page), card);

    append_history_message(ui, "assistant", "Load a session to view the conversation as bubbles.");

    g_signal_connect(refresh_button, "clicked", G_CALLBACK(on_refresh_history_clicked), ui);
    return page;
}

static GtkWidget *
build_tools_view(LiaraWindow *ui)
{
    GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    GtkWidget *card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    GtkWidget *grid = gtk_grid_new();
    GtkWidget *list_button = gtk_button_new_with_label("List Tools");
    GtkWidget *invoke_button = gtk_button_new_with_label("Invoke Tool");
    GtkWidget *params_view = make_editor_view(&ui->tool_params_input, "composer-input", TRUE);
    GtkWidget *output_view = make_editor_view(&ui->tools_output, "response-view", FALSE);
    GtkWidget *button_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);

    ui->tool_name_entry = GTK_ENTRY(gtk_entry_new());
    ui->tool_timeout = GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1, 120, 1));

    gtk_editable_set_text(GTK_EDITABLE(ui->tool_name_entry), "current_time");
    gtk_spin_button_set_value(ui->tool_timeout, 30);
    set_text_view_text(ui->tool_params_input, "{}");

    gtk_widget_add_css_class(page, "content-page");
    gtk_widget_add_css_class(card, "surface-card");
    gtk_grid_set_row_spacing(GTK_GRID(grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(grid), 8);
    gtk_grid_attach(GTK_GRID(grid), gtk_label_new("Tool"), 0, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->tool_name_entry), 1, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), gtk_label_new("Timeout"), 2, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->tool_timeout), 3, 0, 1, 1);

    gtk_box_append(GTK_BOX(button_row), list_button);
    gtk_box_append(GTK_BOX(button_row), invoke_button);

    gtk_box_append(GTK_BOX(card), make_panel_title("tooling", "Tools", "Direct API-backed invocation from the desktop shell"));
    gtk_box_append(GTK_BOX(card), grid);
    gtk_box_append(GTK_BOX(card), button_row);
    gtk_box_append(GTK_BOX(card), gtk_label_new("Parameters JSON"));
    gtk_box_append(GTK_BOX(card), params_view);
    gtk_box_append(GTK_BOX(card), output_view);
    gtk_box_append(GTK_BOX(page), card);

    g_signal_connect(list_button, "clicked", G_CALLBACK(on_list_tools_clicked), ui);
    g_signal_connect(invoke_button, "clicked", G_CALLBACK(on_invoke_tool_clicked), ui);
    return page;
}

static GtkWidget *
build_status_panel(LiaraWindow *ui)
{
    GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    GtkWidget *button_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *config_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget *config_grid = gtk_grid_new();
    GtkWidget *session_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget *session_grid = gtk_grid_new();
    GtkWidget *health_button = gtk_button_new_with_label("Health");
    GtkWidget *backends_button = gtk_button_new_with_label("Backends");
    GtkWidget *load_session_button = gtk_button_new_with_label("Load Session");
    GtkWidget *save_session_button = gtk_button_new_with_label("Save Session");
    GtkWidget *session_button_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *apply_button = gtk_button_new_with_label("Apply");
    GtkWidget *output_view = make_editor_view(&ui->status_output, "inspector-view", FALSE);

    ui->api_base_url_entry = GTK_ENTRY(gtk_entry_new());
    ui->api_host_entry = GTK_ENTRY(gtk_entry_new());
    ui->api_port_spin = GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1, 65535, 1));
    ui->api_endpoint_hint = GTK_LABEL(gtk_label_new("Current endpoint: http://127.0.0.1:8010"));
    ui->session_info_session_entry = GTK_ENTRY(gtk_entry_new());
    ui->session_info_user_entry = GTK_ENTRY(gtk_entry_new());
    ui->session_sandbox_entry = GTK_ENTRY(gtk_entry_new());

    gtk_widget_add_css_class(panel, "inspector");
    gtk_widget_add_css_class(config_card, "config-card");
    gtk_widget_add_css_class(session_card, "config-card");
    gtk_widget_add_css_class(apply_button, "suggested-action");
    gtk_grid_set_row_spacing(GTK_GRID(session_grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(session_grid), 8);
    gtk_grid_set_row_spacing(GTK_GRID(config_grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(config_grid), 8);
    gtk_label_set_xalign(ui->api_endpoint_hint, 0.0f);

    gtk_editable_set_text(GTK_EDITABLE(ui->api_base_url_entry), "http://127.0.0.1:8010");
    gtk_editable_set_text(GTK_EDITABLE(ui->api_host_entry), "127.0.0.1");
    gtk_spin_button_set_value(ui->api_port_spin, 8010);
    gtk_editable_set_text(GTK_EDITABLE(ui->session_info_session_entry), "session-desktop");
    gtk_editable_set_text(GTK_EDITABLE(ui->session_info_user_entry), "user-desktop");
    gtk_editable_set_text(GTK_EDITABLE(ui->session_sandbox_entry), "frontend");

    gtk_box_append(GTK_BOX(panel), make_panel_title("system", "Status", "API and memory backends in one glance"));
    gtk_box_append(GTK_BOX(config_card), make_panel_title("connection", "Endpoint", "Desktop-style local connection settings"));
    gtk_grid_attach(GTK_GRID(config_grid), gtk_label_new("Base URL"), 0, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(config_grid), GTK_WIDGET(ui->api_base_url_entry), 1, 0, 2, 1);
    gtk_grid_attach(GTK_GRID(config_grid), gtk_label_new("Host"), 0, 1, 1, 1);
    gtk_grid_attach(GTK_GRID(config_grid), GTK_WIDGET(ui->api_host_entry), 1, 1, 1, 1);
    gtk_grid_attach(GTK_GRID(config_grid), gtk_label_new("Port"), 2, 1, 1, 1);
    gtk_grid_attach(GTK_GRID(config_grid), GTK_WIDGET(ui->api_port_spin), 3, 1, 1, 1);
    gtk_box_append(GTK_BOX(config_card), config_grid);
    gtk_box_append(GTK_BOX(config_card), GTK_WIDGET(ui->api_endpoint_hint));
    gtk_box_append(GTK_BOX(config_card), apply_button);
    gtk_box_append(GTK_BOX(panel), config_card);
    gtk_box_append(GTK_BOX(session_card), make_panel_title("session", "Session", "Load or persist the active session snapshot"));
    gtk_grid_attach(GTK_GRID(session_grid), gtk_label_new("Session ID"), 0, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(session_grid), GTK_WIDGET(ui->session_info_session_entry), 1, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(session_grid), gtk_label_new("User ID"), 0, 1, 1, 1);
    gtk_grid_attach(GTK_GRID(session_grid), GTK_WIDGET(ui->session_info_user_entry), 1, 1, 1, 1);
    gtk_grid_attach(GTK_GRID(session_grid), gtk_label_new("Sandbox"), 0, 2, 1, 1);
    gtk_grid_attach(GTK_GRID(session_grid), GTK_WIDGET(ui->session_sandbox_entry), 1, 2, 1, 1);
    gtk_box_append(GTK_BOX(session_card), session_grid);
    gtk_box_append(GTK_BOX(session_button_row), load_session_button);
    gtk_box_append(GTK_BOX(session_button_row), save_session_button);
    gtk_box_append(GTK_BOX(session_card), session_button_row);
    gtk_box_append(GTK_BOX(panel), session_card);
    gtk_box_append(GTK_BOX(button_row), health_button);
    gtk_box_append(GTK_BOX(button_row), backends_button);
    gtk_box_append(GTK_BOX(panel), button_row);
    gtk_box_append(GTK_BOX(panel), output_view);

    g_signal_connect(health_button, "clicked", G_CALLBACK(on_health_clicked), ui);
    g_signal_connect(backends_button, "clicked", G_CALLBACK(on_health_backends_clicked), ui);
    g_signal_connect(apply_button, "clicked", G_CALLBACK(on_apply_connection_clicked), ui);
    g_signal_connect(load_session_button, "clicked", G_CALLBACK(on_load_session_clicked), ui);
    g_signal_connect(save_session_button, "clicked", G_CALLBACK(on_save_session_clicked), ui);
    return panel;
}

static GtkWidget *
build_history_side_panel(LiaraWindow *ui)
{
    GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    (void) ui;
    gtk_widget_add_css_class(panel, "inspector");
    gtk_box_append(GTK_BOX(panel), make_panel_title("context", "History Notes", "Use the history page to inspect current session state"));
    gtk_box_append(GTK_BOX(panel), gtk_label_new("Tip: keep the same session id in Chat and History to replay the current conversation."));
    return panel;
}

static GtkWidget *
build_tools_side_panel(LiaraWindow *ui)
{
    GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    (void) ui;
    gtk_widget_add_css_class(panel, "inspector");
    gtk_box_append(GTK_BOX(panel), make_panel_title("developer", "Tool Console", "Manual tool execution with JSON parameters"));
    gtk_box_append(GTK_BOX(panel), gtk_label_new("Tip: the output panel shows the raw API response to keep debugging honest."));
    return panel;
}

static void
on_window_destroy(GtkWidget *widget, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) widget;
    if (ui->stream_watchdog_source_id != 0) {
        g_source_remove(ui->stream_watchdog_source_id);
        ui->stream_watchdog_source_id = 0;
    }
    liara_api_free(ui->api);
    g_clear_pointer(&ui->config_path, g_free);
    g_free(ui);
}

GtkWidget *
liara_window_new(GtkApplication *app)
{
    LiaraWindow *ui = g_new0(LiaraWindow, 1);
    GtkWidget *window = gtk_application_window_new(app);
    GtkWidget *root = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    GtkWidget *sidebar = build_sidebar(ui);
    GtkWidget *center = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    GtkWidget *inspector_shell = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);

    ui->app = app;
    ui->window = window;
    ui->api = liara_api_new("http://127.0.0.1:8010");
    ui->config_path = build_default_config_path();

    ui->main_stack = GTK_STACK(gtk_stack_new());
    ui->side_stack = GTK_STACK(gtk_stack_new());

    gtk_window_set_title(GTK_WINDOW(window), "LIARA GTK UI");
    gtk_window_set_default_size(GTK_WINDOW(window), 1440, 900);
    gtk_window_set_child(GTK_WINDOW(window), root);

    gtk_widget_add_css_class(window, "liara-window");
    gtk_widget_add_css_class(center, "center-shell");
    gtk_widget_add_css_class(inspector_shell, "inspector-shell");
    gtk_widget_set_hexpand(GTK_WIDGET(ui->main_stack), TRUE);
    gtk_widget_set_vexpand(GTK_WIDGET(ui->main_stack), TRUE);
    gtk_widget_set_size_request(sidebar, 240, -1);
    gtk_widget_set_size_request(inspector_shell, 340, -1);

    gtk_stack_add_named(ui->main_stack, build_chat_view(ui), "chat");
    gtk_stack_add_named(ui->main_stack, build_history_view(ui), "history");
    gtk_stack_add_named(ui->main_stack, build_tools_view(ui), "tools");
    gtk_stack_set_visible_child_name(ui->main_stack, "chat");

    gtk_stack_add_named(ui->side_stack, build_status_panel(ui), "status");
    gtk_stack_add_named(ui->side_stack, build_history_side_panel(ui), "history-side");
    gtk_stack_add_named(ui->side_stack, build_tools_side_panel(ui), "tools");
    gtk_stack_set_visible_child_name(ui->side_stack, "status");

    gtk_box_append(GTK_BOX(center), GTK_WIDGET(ui->main_stack));
    gtk_box_append(GTK_BOX(inspector_shell), GTK_WIDGET(ui->side_stack));

    gtk_box_append(GTK_BOX(root), sidebar);
    gtk_box_append(GTK_BOX(root), center);
    gtk_box_append(GTK_BOX(root), inspector_shell);

    load_connection_config(ui);
    g_signal_connect(window, "destroy", G_CALLBACK(on_window_destroy), ui);
    return window;
}
