#include "liara_window.h"
#include "liara_api.h"
#include "liara_chat_input.h"
#include "liara_formatted_text.h"
#include "liara_workspace_explorer.h"

#include <errno.h>
#include <stdlib.h>
#include <json-glib/json-glib.h>

#ifdef G_OS_WIN32
#ifndef SECURITY_WIN32
#define SECURITY_WIN32
#endif
#include <windows.h>
#include <secext.h>
#endif

/* Forward declarations */
static void on_send_chat_clicked(GtkButton *button, gpointer user_data);

typedef struct {
    GtkApplication *app;
    GtkWidget *window;
    gboolean dev_mode;
    LiaraApi *api;
    char *config_path;

    GtkStack *main_stack;
    GtkStack *side_stack;
    GtkRevealer *inspector_revealer;
    GtkWidget *inspector_toggle_button;

    GtkEntry *session_entry;
    GtkEntry *user_entry;
    GtkTextView *chat_input;
    GtkSpinButton *max_tokens;
    GtkScrolledWindow *chat_transcript_scroller;
    GtkWidget *chat_messages_box;
    GtkWidget *chat_send_button;
    GtkWidget *current_assistant_content;
    GtkWidget *current_assistant_meta_box;
    GtkLabel *current_assistant_label;
    gboolean current_assistant_started;
    gboolean stream_inflight;
    guint stream_watchdog_source_id;
    volatile gint64 stream_last_event_usec;
    volatile gboolean stream_api_heartbeat_seen;

    /* New: Chat Input Handler */
    LiaraChatInput *chat_input_handler;

    GtkEntry *history_session_entry;
    GtkEntry *history_run_id_entry;
    GtkSpinButton *history_limit;
    GtkCheckButton *history_include_tools;
    GtkScrolledWindow *history_scroller;
    GtkWidget *history_messages_box;

    GtkEntry *explorer_path_entry;
    GtkLabel *explorer_status_label;
    GtkWidget *explorer_list_box;
    GtkTextView *explorer_preview;
    
    /* New: Workspace Explorer */
    LiaraWorkspaceExplorer *workspace_explorer;

    GtkTextView *status_output;
    GtkLabel *stream_status_badge;
    GtkLabel *stream_status_detail;
    GtkWidget *status_health_cards;
    GtkLabel *status_health_summary;
    GtkLabel *identity_display_name;
    GtkLabel *identity_login_name;
    GtkEntry *api_base_url_entry;
    GtkEntry *api_host_entry;
    GtkSpinButton *api_port_spin;
    GtkLabel *api_endpoint_hint;
    GtkEntry *session_info_session_entry;
    GtkEntry *session_info_user_entry;
    GtkEntry *session_sandbox_entry;
    GtkEntry *session_meta_profile_entry;
    GtkEntry *session_meta_workspace_entry;
    GtkEntry *session_meta_notes_entry;
    GtkLabel *session_snapshot_count;
    GtkLabel *session_snapshot_last_run;
    GtkLabel *session_snapshot_updated;
    GtkLabel *session_snapshot_history;
    GtkLabel *session_snapshot_sandbox;

    GtkTextView *tools_output;
    GtkWidget *tool_list_box;
    GtkLabel *tool_detail_name;
    GtkLabel *tool_detail_description;
    GtkWidget *tool_required_params_box;
    GtkWidget *tool_optional_params_box;
    GtkWidget *tool_form_box;
    GtkEntry *tool_name_entry;
    GtkTextView *tool_params_input;
    GtkSpinButton *tool_timeout;

    GtkTextView *audit_output;
    GtkSpinButton *audit_limit;
    GtkSpinButton *audit_max_items;
    GtkCheckButton *audit_blocked_only;
    GtkEntry *audit_source_entry;
    GtkEntry *audit_risk_entry;
    GtkEntry *audit_family_entry;
    GtkEntry *audit_preset_entry;

    GtkCheckButton *settings_stream_enabled;
    GtkSpinButton *settings_default_tokens;
    GtkCheckButton *settings_history_include_tools;
    GtkLabel *settings_saved_hint;

    gboolean client_stream_enabled;
    int client_default_max_tokens;
    gboolean client_history_include_tools;
    int client_stream_watchdog_seconds;
} LiaraWindow;

typedef enum {
    EXPLORER_REQUEST_LIST = 0,
    EXPLORER_REQUEST_FILE = 1,
} ExplorerRequestKind;

typedef struct {
    LiaraWindow *ui;
    ExplorerRequestKind kind;
    char *path;
} ExplorerRequestContext;

typedef struct {
    char *path;
    gboolean is_dir;
} ExplorerItemData;

static void set_text_view_text(GtkTextView *view, const char *text);
static char *get_text_view_text(GtkTextView *view);
static void on_chat_stream_chunk(const char *chunk_text, gpointer user_data);
static void on_chat_stream_progress(const char *progress_payload, gpointer user_data);
static void on_chat_stream_heartbeat(const char *heartbeat_payload, gpointer user_data);
static void on_chat_stream_complete(const char *final_payload, GError *error, gpointer user_data);
static void render_message_content(GtkWidget *content_box, const char *text, gboolean assistant);
static void set_stream_status(LiaraWindow *ui, const char *state, const char *detail, const char *variant);
static void render_assistant_metadata(GtkWidget *meta_box, const char *final_payload);
static void render_artifacts_from_chat_payload(GtkWidget *content_box, const char *payload, LiaraApi *api);
static void render_health_cards(GtkWidget *container, GtkLabel *summary_label, const char *response_text, gboolean backend_mode);
static void request_startup_greeting(LiaraWindow *ui);
static void on_startup_greeting_loaded(const char *response_text, GError *error, gpointer user_data);
static void on_tools_loaded(const char *response_text, GError *error, gpointer user_data);
static void on_tool_metadata_loaded(const char *response_text, GError *error, gpointer user_data);
static void on_history_loaded(const char *response_text, GError *error, gpointer user_data);
static void on_explorer_response(const char *response_text, GError *error, gpointer user_data);
static void on_status_health_loaded(const char *response_text, GError *error, gpointer user_data);
static void on_status_backends_loaded(const char *response_text, GError *error, gpointer user_data);
static void on_apply_connection_clicked(GtkButton *button, gpointer user_data);
static void on_apply_client_settings_clicked(GtkButton *button, gpointer user_data);
static void on_audit_summary_clicked(GtkButton *button, gpointer user_data);
static void on_audit_suspicious_clicked(GtkButton *button, gpointer user_data);
static void on_audit_preset_clicked(GtkButton *button, gpointer user_data);
static void on_audit_response_loaded(const char *response_text, GError *error, gpointer user_data);
static void on_load_session_clicked(GtkButton *button, gpointer user_data);
static void on_save_session_clicked(GtkButton *button, gpointer user_data);
static void on_session_snapshot_loaded(const char *response_text, GError *error, gpointer user_data);
static void on_toggle_inspector_clicked(GtkButton *button, gpointer user_data);
static void on_explorer_refresh_clicked(GtkButton *button, gpointer user_data);
static void on_explorer_home_clicked(GtkButton *button, gpointer user_data);
static void on_explorer_workspace_clicked(GtkButton *button, gpointer user_data);
static void on_explorer_up_clicked(GtkButton *button, gpointer user_data);
static void on_explorer_open_path_clicked(GtkButton *button, gpointer user_data);
static void on_explorer_row_activated(GtkListBox *box, GtkListBoxRow *row, gpointer user_data);
static void on_chat_request_loaded(const char *response_text, GError *error, gpointer user_data);
static GtkWidget *wrap_side_panel(GtkWidget *child);
static void apply_windows_identity_defaults(LiaraWindow *ui);
static gboolean save_connection_config(LiaraWindow *ui, const char *base_url, GError **error);
static void populate_dynamic_tool_form(LiaraWindow *ui, JsonArray *required, JsonArray *optional);
static GtkWidget *create_metadata_chip(const char *text);
static const char *format_message_role_label(const char *role, gboolean assistant);
static void switch_view(LiaraWindow *ui, const char *main_name, const char *side_name);

static void
set_snapshot_value(GtkLabel *label, const char *prefix, const char *value)
{
    g_autofree char *text = NULL;

    if (label == NULL) {
        return;
    }

    text = g_strdup_printf("%s%s", prefix, (value != NULL && value[0] != '\0') ? value : "-");
    gtk_label_set_text(label, text);
}

static void
set_snapshot_int_value(GtkLabel *label, const char *prefix, gint64 value)
{
    g_autofree char *text = NULL;

    if (label == NULL) {
        return;
    }

    text = g_strdup_printf("%s%" G_GINT64_FORMAT, prefix, value);
    gtk_label_set_text(label, text);
}

static GtkWidget *
make_tip_label(const char *text)
{
    GtkWidget *label = gtk_label_new(text);

    gtk_label_set_xalign(GTK_LABEL(label), 0.0f);
    gtk_label_set_wrap(GTK_LABEL(label), TRUE);
    gtk_label_set_wrap_mode(GTK_LABEL(label), PANGO_WRAP_WORD_CHAR);
    gtk_label_set_max_width_chars(GTK_LABEL(label), 48);
    return label;
}

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

static void
set_stream_status(LiaraWindow *ui, const char *state, const char *detail, const char *variant)
{
    if (ui == NULL || ui->stream_status_badge == NULL || ui->stream_status_detail == NULL) {
        return;
    }

    gtk_widget_remove_css_class(GTK_WIDGET(ui->stream_status_badge), "stream-status-idle");
    gtk_widget_remove_css_class(GTK_WIDGET(ui->stream_status_badge), "stream-status-active");
    gtk_widget_remove_css_class(GTK_WIDGET(ui->stream_status_badge), "stream-status-heartbeat");
    gtk_widget_remove_css_class(GTK_WIDGET(ui->stream_status_badge), "stream-status-complete");
    gtk_widget_remove_css_class(GTK_WIDGET(ui->stream_status_badge), "stream-status-error");

    gtk_label_set_text(ui->stream_status_badge, state != NULL ? state : "STREAM");
    gtk_label_set_text(ui->stream_status_detail, detail != NULL ? detail : "");

    if (variant != NULL && variant[0] != '\0') {
        gtk_widget_add_css_class(GTK_WIDGET(ui->stream_status_badge), variant);
    }
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
build_artifact_local_path(JsonObject *artifact)
{
    JsonObject *metadata;
    const char *stored_path;
    const char *root_env;
    g_autofree char *cwd = NULL;

    if (artifact == NULL || !json_object_has_member(artifact, "metadata")) {
        return NULL;
    }

    metadata = json_object_get_object_member(artifact, "metadata");
    if (metadata == NULL || !json_object_has_member(metadata, "stored_path")) {
        return NULL;
    }

    stored_path = json_object_get_string_member(metadata, "stored_path");
    if (stored_path == NULL || stored_path[0] == '\0') {
        return NULL;
    }

    if (g_path_is_absolute(stored_path)) {
        return g_strdup(stored_path);
    }

    root_env = g_getenv("LIARA_READ_ROOT");
    if (root_env == NULL || root_env[0] == '\0') {
        cwd = g_get_current_dir();
        root_env = cwd;
    }

    return g_build_filename(root_env, stored_path, NULL);
}

static char *
build_artifact_uri(LiaraApi *api, const char *url)
{
    const char *base_url;
    g_autofree char *normalized_base_url = NULL;

    if (url == NULL || url[0] == '\0') {
        return NULL;
    }

    if (g_str_has_prefix(url, "http://") || g_str_has_prefix(url, "https://")) {
        return g_strdup(url);
    }

    base_url = liara_api_get_base_url(api);
    if (base_url == NULL || base_url[0] == '\0') {
        return g_strdup(url);
    }

    return g_strdup_printf("%s%s", base_url, url);
}

static void
render_artifacts_from_chat_payload(GtkWidget *content_box, const char *payload, LiaraApi *api)
{
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonObject *root;
    JsonArray *artifacts;
    guint index;

    if (content_box == NULL || payload == NULL || !json_parser_load_from_data(parser, payload, -1, NULL)) {
        return;
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL || !json_object_has_member(root, "artifacts")) {
        return;
    }

    artifacts = json_object_get_array_member(root, "artifacts");
    if (artifacts == NULL || json_array_get_length(artifacts) == 0) {
        return;
    }

    for (index = 0; index < json_array_get_length(artifacts); index++) {
        JsonObject *artifact = json_array_get_object_element(artifacts, index);
        const char *kind;
        const char *title;
        const char *url;
        GtkWidget *artifact_box;
        GtkWidget *title_label;
        g_autofree char *local_path = NULL;
        g_autofree char *artifact_uri = NULL;

        if (artifact == NULL) {
            continue;
        }

        kind = json_object_has_member(artifact, "kind")
            ? json_object_get_string_member(artifact, "kind")
            : NULL;
        if (g_strcmp0(kind, "image") != 0) {
            continue;
        }

        title = json_object_has_member(artifact, "title")
            ? json_object_get_string_member(artifact, "title")
            : "Chart";
        url = json_object_has_member(artifact, "url")
            ? json_object_get_string_member(artifact, "url")
            : NULL;

        artifact_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
        gtk_widget_add_css_class(artifact_box, "assistant-artifact-box");
        gtk_widget_set_margin_top(artifact_box, 8);

        title_label = gtk_label_new(title != NULL && title[0] != '\0' ? title : "Chart");
        gtk_widget_add_css_class(title_label, "meta-chip");
        gtk_label_set_xalign(GTK_LABEL(title_label), 0.0f);
        gtk_box_append(GTK_BOX(artifact_box), title_label);

        local_path = build_artifact_local_path(artifact);
        if (local_path != NULL && g_file_test(local_path, G_FILE_TEST_IS_REGULAR)) {
            GtkWidget *picture = gtk_picture_new_for_filename(local_path);
            gtk_picture_set_can_shrink(GTK_PICTURE(picture), TRUE);
            gtk_widget_set_size_request(picture, 520, -1);
            gtk_widget_set_hexpand(picture, TRUE);
            gtk_widget_add_css_class(picture, "assistant-artifact-image");
            gtk_box_append(GTK_BOX(artifact_box), picture);
        } else {
            artifact_uri = build_artifact_uri(api, url);
            if (artifact_uri != NULL) {
                GtkWidget *link = gtk_link_button_new_with_label(artifact_uri, "Open artifact");
                gtk_box_append(GTK_BOX(artifact_box), link);
            } else {
                GtkWidget *fallback = gtk_label_new("Artifact preview unavailable.");
                gtk_label_set_xalign(GTK_LABEL(fallback), 0.0f);
                gtk_box_append(GTK_BOX(artifact_box), fallback);
            }
        }

        gtk_box_append(GTK_BOX(content_box), artifact_box);
    }
}

static char *
build_default_config_path(void)
{
    if (g_path_is_absolute(g_get_prgname())) {
        g_autofree char *exe_dir = g_path_get_dirname(g_get_prgname());
        g_autofree char *parent_dir = g_path_get_dirname(exe_dir);
        g_autofree char *dist_config_dir = g_build_filename(parent_dir, "config", NULL);
        g_autofree char *parent_dist_config_dir = g_build_filename(parent_dir, "dist", "config", NULL);

        if (g_file_test(dist_config_dir, G_FILE_TEST_IS_DIR)) {
            return g_build_filename(parent_dir, "config", "lserv.json", NULL);
        }

        if (g_file_test(parent_dist_config_dir, G_FILE_TEST_IS_DIR)) {
            return g_build_filename(parent_dir, "dist", "config", "lserv.json", NULL);
        }

        return g_build_filename(exe_dir, "config", "lserv.json", NULL);
    }

    {
        g_autofree char *cwd = g_get_current_dir();
        return g_build_filename(cwd, "config", "lserv.json", NULL);
    }
}

static char *
normalize_id_component(const char *text, char separator)
{
    GString *out = g_string_new("");
    gboolean previous_sep = FALSE;
    const char *cursor = text != NULL ? text : "";

    while (*cursor != '\0') {
        gunichar ch = g_utf8_get_char(cursor);

        if (g_unichar_isalnum(ch)) {
            g_string_append_unichar(out, g_unichar_tolower(ch));
            previous_sep = FALSE;
        } else if (!previous_sep && out->len > 0) {
            g_string_append_c(out, separator);
            previous_sep = TRUE;
        }

        cursor = g_utf8_next_char(cursor);
    }

    while (out->len > 0 && out->str[out->len - 1] == separator) {
        g_string_truncate(out, out->len - 1);
    }

    if (out->len == 0) {
        g_string_append(out, "user");
    }

    return g_string_free(out, FALSE);
}

#ifdef G_OS_WIN32
static char *
utf8_from_wide(const wchar_t *value)
{
    if (value == NULL || value[0] == L'\0') {
        return NULL;
    }

    return g_utf16_to_utf8((const gunichar2 *) value, -1, NULL, NULL, NULL);
}

static char *
get_windows_name_extended(EXTENDED_NAME_FORMAT format)
{
    ULONG size = 0;
    wchar_t *buffer;
    char *utf8;

    GetUserNameExW(format, NULL, &size);
    if (size == 0) {
        return NULL;
    }

    buffer = g_new0(wchar_t, size + 1);
    if (!GetUserNameExW(format, buffer, &size)) {
        g_free(buffer);
        return NULL;
    }

    utf8 = utf8_from_wide(buffer);
    g_free(buffer);
    return utf8;
}

static char *
get_windows_login_name(void)
{
    DWORD size = 0;
    wchar_t *buffer;
    char *utf8;

    GetUserNameW(NULL, &size);
    if (size == 0) {
        return g_strdup(g_getenv("USERNAME"));
    }

    buffer = g_new0(wchar_t, size + 1);
    if (!GetUserNameW(buffer, &size)) {
        g_free(buffer);
        return g_strdup(g_getenv("USERNAME"));
    }

    utf8 = utf8_from_wide(buffer);
    g_free(buffer);
    return utf8 != NULL ? utf8 : g_strdup(g_getenv("USERNAME"));
}

static char *
get_windows_computer_name(void)
{
    DWORD size = 0;
    wchar_t *buffer;
    char *utf8;

    GetComputerNameExW(ComputerNameDnsHostname, NULL, &size);
    if (size == 0) {
        return g_strdup(g_getenv("COMPUTERNAME"));
    }

    buffer = g_new0(wchar_t, size + 1);
    if (!GetComputerNameExW(ComputerNameDnsHostname, buffer, &size)) {
        g_free(buffer);
        return g_strdup(g_getenv("COMPUTERNAME"));
    }

    utf8 = utf8_from_wide(buffer);
    g_free(buffer);
    return utf8 != NULL ? utf8 : g_strdup(g_getenv("COMPUTERNAME"));
}
#endif

static void
apply_windows_identity_defaults(LiaraWindow *ui)
{
    g_autofree char *display_name = NULL;
    g_autofree char *sam_name = NULL;
    g_autofree char *login_name = NULL;
    g_autofree char *computer_name = NULL;
    g_autofree char *domain_name = NULL;
    g_autofree char *normalized_user_id = NULL;
    g_autofree char *normalized_computer = NULL;
    g_autofree char *session_id = NULL;
    g_autofree char *sandbox_name = NULL;

#ifdef G_OS_WIN32
    display_name = get_windows_name_extended(NameDisplay);
    sam_name = get_windows_name_extended(NameSamCompatible);
    login_name = get_windows_login_name();
    computer_name = get_windows_computer_name();
#else
    login_name = g_strdup(g_getenv("USERNAME"));
    computer_name = g_strdup(g_getenv("COMPUTERNAME"));
#endif

    if (display_name == NULL || display_name[0] == '\0') {
        display_name = g_strdup(login_name != NULL ? login_name : "User");
    }

    if (sam_name != NULL && strchr(sam_name, '\\') != NULL) {
        char **parts = g_strsplit(sam_name, "\\", 2);
        if (parts[0] != NULL) {
            domain_name = g_strdup(parts[0]);
        }
        if (parts[1] != NULL && parts[1][0] != '\0') {
            g_clear_pointer(&login_name, g_free);
            login_name = g_strdup(parts[1]);
        }
        g_strfreev(parts);
    }

    if (domain_name == NULL || domain_name[0] == '\0') {
        domain_name = g_strdup(g_getenv("USERDOMAIN"));
    }
    if (login_name == NULL || login_name[0] == '\0') {
        login_name = g_strdup("user");
    }
    if (computer_name == NULL || computer_name[0] == '\0') {
        computer_name = g_strdup("desktop");
    }

    if (domain_name != NULL && domain_name[0] != '\0') {
        g_autofree char *raw_user_id = g_strdup_printf("%s-%s", domain_name, login_name);
        normalized_user_id = normalize_id_component(raw_user_id, '-');
    } else {
        normalized_user_id = normalize_id_component(login_name, '-');
    }

    normalized_computer = normalize_id_component(computer_name, '-');
    session_id = g_strdup_printf("%s-%s", normalized_user_id, normalized_computer);
    sandbox_name = normalize_id_component(normalized_user_id, '_');

    gtk_editable_set_text(GTK_EDITABLE(ui->user_entry), normalized_user_id);
    gtk_editable_set_text(GTK_EDITABLE(ui->session_entry), session_id);
    gtk_editable_set_text(GTK_EDITABLE(ui->history_session_entry), session_id);
    gtk_editable_set_text(GTK_EDITABLE(ui->session_info_user_entry), normalized_user_id);
    gtk_editable_set_text(GTK_EDITABLE(ui->session_info_session_entry), session_id);
    gtk_editable_set_text(GTK_EDITABLE(ui->session_sandbox_entry), sandbox_name);
    if (ui->identity_display_name != NULL) {
        gtk_label_set_text(ui->identity_display_name, display_name);
    }
    if (ui->identity_login_name != NULL) {
        g_autofree char *login_text = g_strdup_printf("%s  |  %s", normalized_user_id, session_id);
        gtk_label_set_text(ui->identity_login_name, login_text);
    }

    if (ui->window != NULL) {
        g_autofree char *title = g_strdup_printf("LIARA GTK UI - %s", display_name);
        gtk_window_set_title(GTK_WINDOW(ui->window), title);
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
        g_autoptr(GUri) parsed = g_uri_parse(url, G_URI_FLAGS_NONE, NULL);
        if (parsed != NULL) {
            const char *scheme = g_uri_get_scheme(parsed);
            const char *host = g_uri_get_host(parsed);
            const char *path = g_uri_get_path(parsed);
            int parsed_port = (int) g_uri_get_port(parsed);
            gboolean looks_like_bridge = FALSE;

            if (path != NULL && (g_str_has_prefix(path, "/v1") || g_str_has_prefix(path, "/openai"))) {
                looks_like_bridge = TRUE;
            }
            if (parsed_port == 8011) {
                looks_like_bridge = TRUE;
            }

            if (looks_like_bridge) {
                if (host != NULL && host[0] != '\0') {
                    return g_strdup_printf("%s://%s:%d", (scheme != NULL && scheme[0] != '\0') ? scheme : "http", host, 8010);
                }
                return g_strdup("http://127.0.0.1:8010");
            }
        }
        return g_strdup(url);
    }

    if (host[0] == '\0') {
        return g_strdup_printf("http://127.0.0.1:%d", MAX(1, port));
    }

    return g_strdup_printf("http://%s:%d", host, MAX(1, port));
}

static gboolean
is_legacy_bridge_base_url(const char *url)
{
    g_autoptr(GUri) parsed = NULL;
    const char *path;
    int parsed_port;

    if (url == NULL || url[0] == '\0') {
        return FALSE;
    }

    parsed = g_uri_parse(url, G_URI_FLAGS_NONE, NULL);
    if (parsed == NULL) {
        return FALSE;
    }

    path = g_uri_get_path(parsed);
    parsed_port = (int) g_uri_get_port(parsed);

    if (parsed_port == 8011) {
        return TRUE;
    }

    if (path != NULL && (g_str_has_prefix(path, "/v1") || g_str_has_prefix(path, "/openai"))) {
        return TRUE;
    }

    return FALSE;
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
sync_client_settings_to_widgets(LiaraWindow *ui)
{
    if (ui->max_tokens != NULL) {
        gtk_spin_button_set_value(ui->max_tokens, ui->client_default_max_tokens);
    }
    if (ui->history_include_tools != NULL) {
        gtk_check_button_set_active(ui->history_include_tools, ui->client_history_include_tools);
    }
    if (ui->settings_default_tokens != NULL) {
        gtk_spin_button_set_value(ui->settings_default_tokens, ui->client_default_max_tokens);
    }
    if (ui->settings_stream_enabled != NULL) {
        gtk_check_button_set_active(ui->settings_stream_enabled, ui->client_stream_enabled);
    }
    if (ui->settings_history_include_tools != NULL) {
        gtk_check_button_set_active(ui->settings_history_include_tools, ui->client_history_include_tools);
    }
}

static void
sync_client_settings_from_widgets(LiaraWindow *ui)
{
    if (ui->settings_default_tokens != NULL) {
        ui->client_default_max_tokens = gtk_spin_button_get_value_as_int(ui->settings_default_tokens);
    }
    if (ui->settings_stream_enabled != NULL) {
        ui->client_stream_enabled = gtk_check_button_get_active(ui->settings_stream_enabled);
    }
    if (ui->settings_history_include_tools != NULL) {
        ui->client_history_include_tools = gtk_check_button_get_active(ui->settings_history_include_tools);
    }
}

static void
load_connection_config(LiaraWindow *ui)
{
    g_autoptr(JsonParser) parser = json_parser_new();
    g_autoptr(GError) error = NULL;
    JsonObject *root;
    const char *base_url;
    g_autofree char *normalized_base_url = NULL;
    const char *current_base_url = liara_api_get_base_url(ui->api);
    const char *display_name = NULL;
    const char *user_id = NULL;
    const char *session_id = NULL;
    const char *sandbox = NULL;
    const char *watchdog_env = g_getenv("LIARA_STREAM_WATCHDOG_SECONDS");
    gboolean migrated_from_bridge = FALSE;

    ui->client_default_max_tokens = 2048;
    ui->client_stream_enabled = TRUE;
    ui->client_history_include_tools = TRUE;
    ui->client_stream_watchdog_seconds = 120;

    if (watchdog_env != NULL && watchdog_env[0] != '\0') {
        char *endptr = NULL;
        long parsed = strtol(watchdog_env, &endptr, 10);
        if (endptr != watchdog_env && (endptr == NULL || *endptr == '\0')) {
            ui->client_stream_watchdog_seconds = (int) CLAMP(parsed, 15, 900);
        }
    }

    if (ui->config_path == NULL || !g_file_test(ui->config_path, G_FILE_TEST_EXISTS)) {
        sync_endpoint_controls_from_base_url(ui, current_base_url);
        save_connection_config(ui, current_base_url, NULL);
        return;
    }

    if (!json_parser_load_from_file(parser, ui->config_path, &error)) {
        sync_endpoint_controls_from_base_url(ui, current_base_url);
        save_connection_config(ui, current_base_url, NULL);
        return;
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL || !json_object_has_member(root, "api_base_url")) {
        sync_endpoint_controls_from_base_url(ui, current_base_url);
        save_connection_config(ui, current_base_url, NULL);
        return;
    }

    base_url = json_object_get_string_member(root, "api_base_url");
    migrated_from_bridge = is_legacy_bridge_base_url(base_url);
    normalized_base_url = normalize_base_url(base_url, NULL, 8010);
    liara_api_set_base_url(ui->api, normalized_base_url);
    sync_endpoint_controls_from_base_url(ui, normalized_base_url);
    if (g_strcmp0(base_url, normalized_base_url) != 0) {
        save_connection_config(ui, normalized_base_url, NULL);
    }

    if (json_object_has_member(root, "display_name") && ui->identity_display_name != NULL) {
        display_name = json_object_get_string_member(root, "display_name");
        if (display_name != NULL && display_name[0] != '\0') {
            gtk_label_set_text(ui->identity_display_name, display_name);
        }
    }
    if (json_object_has_member(root, "user_id")) {
        user_id = json_object_get_string_member(root, "user_id");
        if (user_id != NULL && user_id[0] != '\0') {
            gtk_editable_set_text(GTK_EDITABLE(ui->user_entry), user_id);
            gtk_editable_set_text(GTK_EDITABLE(ui->session_info_user_entry), user_id);
        }
    }
    if (json_object_has_member(root, "session_id")) {
        session_id = json_object_get_string_member(root, "session_id");
        if (session_id != NULL && session_id[0] != '\0') {
            gtk_editable_set_text(GTK_EDITABLE(ui->session_entry), session_id);
            gtk_editable_set_text(GTK_EDITABLE(ui->history_session_entry), session_id);
            gtk_editable_set_text(GTK_EDITABLE(ui->session_info_session_entry), session_id);
        }
    }
    if (json_object_has_member(root, "sandbox")) {
        sandbox = json_object_get_string_member(root, "sandbox");
        if (sandbox != NULL && sandbox[0] != '\0') {
            gtk_editable_set_text(GTK_EDITABLE(ui->session_sandbox_entry), sandbox);
        }
    }

    if (json_object_has_member(root, "chat_default_max_tokens")) {
        int loaded_tokens = (int) json_object_get_int_member(root, "chat_default_max_tokens");
        ui->client_default_max_tokens = CLAMP(loaded_tokens, 1, 8192);
    }

    if (json_object_has_member(root, "chat_stream_enabled")) {
        ui->client_stream_enabled = json_object_get_boolean_member(root, "chat_stream_enabled");
    }

    if (json_object_has_member(root, "history_include_tools_default")) {
        ui->client_history_include_tools = json_object_get_boolean_member(root, "history_include_tools_default");
    }

    if (json_object_has_member(root, "chat_stream_watchdog_seconds")) {
        int loaded_watchdog = (int) json_object_get_int_member(root, "chat_stream_watchdog_seconds");
        ui->client_stream_watchdog_seconds = CLAMP(loaded_watchdog, 15, 900);
    }

    sync_client_settings_to_widgets(ui);

    if (migrated_from_bridge) {
        g_autofree char *message = g_strdup_printf(
            "Legacy bridge endpoint detected and migrated.\n\nOld endpoint: %s\nNew direct API endpoint: %s\n\nWMTool-Liara now uses direct API mode (/chat, /chat/stream) on port 8010.",
            base_url != NULL ? base_url : "(empty)",
            normalized_base_url
        );
        if (ui->settings_saved_hint != NULL) {
            gtk_label_set_text(ui->settings_saved_hint, "Migrated legacy bridge endpoint to direct API (8010).");
        }
        set_text_view_text(ui->status_output, message);
    } else if (ui->dev_mode && normalized_base_url != NULL) {
        g_autofree char *message = g_strdup_printf(
            "DEV mode active.\nDirect API endpoint: %s\n\nNo universal bridge client is used in WMTool-Liara runtime.",
            normalized_base_url
        );
        set_text_view_text(ui->status_output, message);
    }
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

    sync_client_settings_from_widgets(ui);

    json_builder_begin_object(builder);
    json_builder_set_member_name(builder, "api_base_url");
    json_builder_add_string_value(builder, base_url);
    json_builder_set_member_name(builder, "dev_mode");
    json_builder_add_boolean_value(builder, ui->dev_mode);
    json_builder_set_member_name(builder, "display_name");
    json_builder_add_string_value(
        builder,
        ui->identity_display_name != NULL ? gtk_label_get_text(ui->identity_display_name) : ""
    );
    json_builder_set_member_name(builder, "user_id");
    json_builder_add_string_value(builder, gtk_editable_get_text(GTK_EDITABLE(ui->user_entry)));
    json_builder_set_member_name(builder, "session_id");
    json_builder_add_string_value(builder, gtk_editable_get_text(GTK_EDITABLE(ui->session_entry)));
    json_builder_set_member_name(builder, "sandbox");
    json_builder_add_string_value(builder, gtk_editable_get_text(GTK_EDITABLE(ui->session_sandbox_entry)));
    json_builder_set_member_name(builder, "chat_default_max_tokens");
    json_builder_add_int_value(builder, ui->client_default_max_tokens);
    json_builder_set_member_name(builder, "chat_stream_enabled");
    json_builder_add_boolean_value(builder, ui->client_stream_enabled);
    json_builder_set_member_name(builder, "history_include_tools_default");
    json_builder_add_boolean_value(builder, ui->client_history_include_tools);
    json_builder_set_member_name(builder, "chat_stream_watchdog_seconds");
    json_builder_add_int_value(builder, CLAMP(ui->client_stream_watchdog_seconds, 15, 900));
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
        if (GTK_IS_BOX(widget)) {
            gtk_box_remove(GTK_BOX(widget), child);
        } else if (GTK_IS_LIST_BOX(widget)) {
            gtk_list_box_remove(GTK_LIST_BOX(widget), child);
        } else {
            gtk_widget_unparent(child);
        }
        child = next;
    }
}

static void
explorer_item_data_free(ExplorerItemData *item)
{
    if (item == NULL) {
        return;
    }
    g_clear_pointer(&item->path, g_free);
    g_free(item);
}

static void
explorer_request_context_free(ExplorerRequestContext *context)
{
    if (context == NULL) {
        return;
    }
    g_clear_pointer(&context->path, g_free);
    g_free(context);
}

static char *
explorer_normalize_path(const char *raw_path)
{
    g_autofree char *value = g_strdup(raw_path != NULL ? raw_path : "");
    g_strstrip(value);
    if (value[0] == '\0') {
        return g_strdup("/home/liara/workspace");
    }
    return g_strdup(value);
}

static void
set_explorer_status(LiaraWindow *ui, const char *text)
{
    if (ui->explorer_status_label == NULL) {
        return;
    }
    gtk_label_set_text(ui->explorer_status_label, text != NULL ? text : "");
}

static char *
build_sys_invoke_parameters_json(const char *command, const char *path)
{
    g_autoptr(JsonBuilder) builder = json_builder_new();
    g_autoptr(JsonGenerator) generator = json_generator_new();
    g_autoptr(JsonNode) root = NULL;

    json_builder_begin_object(builder);
    json_builder_set_member_name(builder, "command");
    json_builder_add_string_value(builder, command);
    json_builder_set_member_name(builder, "args");
    json_builder_begin_array(builder);

    if (g_strcmp0(command, "ls") == 0) {
        json_builder_add_string_value(builder, "-la");
        json_builder_add_string_value(builder, path);
    } else if (g_strcmp0(command, "cat") == 0) {
        json_builder_add_string_value(builder, path);
    }

    json_builder_end_array(builder);
    json_builder_end_object(builder);

    root = json_builder_get_root(builder);
    json_generator_set_root(generator, root);
    return json_generator_to_data(generator, NULL);
}

static void
request_explorer_list(LiaraWindow *ui, const char *raw_path)
{
    ExplorerRequestContext *context = g_new0(ExplorerRequestContext, 1);
    g_autofree char *path = explorer_normalize_path(raw_path);
    g_autofree char *parameters_json = build_sys_invoke_parameters_json("ls", path);

    context->ui = ui;
    context->kind = EXPLORER_REQUEST_LIST;
    context->path = g_strdup(path);

    gtk_editable_set_text(GTK_EDITABLE(ui->explorer_path_entry), path);
    set_explorer_status(ui, "Loading directory listing ...");
    liara_api_post_tool_invoke(ui->api, "sys", parameters_json, 30, on_explorer_response, context);
}

static void
request_explorer_file_preview(LiaraWindow *ui, const char *path)
{
    ExplorerRequestContext *context = g_new0(ExplorerRequestContext, 1);
    g_autofree char *parameters_json = build_sys_invoke_parameters_json("cat", path);

    context->ui = ui;
    context->kind = EXPLORER_REQUEST_FILE;
    context->path = g_strdup(path);

    set_explorer_status(ui, "Loading file preview ...");
    liara_api_post_tool_invoke(ui->api, "sys", parameters_json, 30, on_explorer_response, context);
}

static char *
extract_ls_display_name(const char *line)
{
    const char *cursor = line;
    int token_count = 0;

    while (*cursor != '\0' && token_count < 8) {
        while (*cursor == ' ') {
            cursor++;
        }
        if (*cursor == '\0') {
            return NULL;
        }
        while (*cursor != '\0' && *cursor != ' ') {
            cursor++;
        }
        token_count++;
    }

    while (*cursor == ' ') {
        cursor++;
    }
    if (*cursor == '\0') {
        return NULL;
    }

    return g_strdup(cursor);
}

static GtkWidget *
create_explorer_row(const char *name, const char *full_path, gboolean is_dir)
{
    GtkWidget *row = gtk_list_box_row_new();
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *icon = gtk_label_new(is_dir ? "[DIR]" : "[FILE]");
    GtkWidget *name_label = gtk_label_new(name);
    ExplorerItemData *item = g_new0(ExplorerItemData, 1);

    gtk_widget_add_css_class(box, "tool-list-row");
    gtk_widget_add_css_class(icon, "meta-chip");
    gtk_widget_add_css_class(name_label, "nav-title");
    gtk_label_set_xalign(GTK_LABEL(icon), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(name_label), 0.0f);

    item->path = g_strdup(full_path);
    item->is_dir = is_dir;
    g_object_set_data_full(G_OBJECT(row), "explorer-item", item, (GDestroyNotify) explorer_item_data_free);

    gtk_box_append(GTK_BOX(box), icon);
    gtk_box_append(GTK_BOX(box), name_label);
    gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(row), box);
    return row;
}

static void
populate_explorer_list_from_ls(LiaraWindow *ui, const char *path, const char *output)
{
    g_auto(GStrv) lines = NULL;
    guint i;
    guint count = 0;

    clear_box_children(ui->explorer_list_box);
    lines = g_strsplit(output != NULL ? output : "", "\n", -1);

    for (i = 0; lines[i] != NULL; i++) {
        const char *line = lines[i];
        g_autofree char *display_name = NULL;
        g_autofree char *full_path = NULL;
        gboolean is_dir;

        if (line[0] == '\0' || g_str_has_prefix(line, "total ")) {
            continue;
        }

        is_dir = line[0] == 'd';
        display_name = extract_ls_display_name(line);
        if (display_name == NULL || display_name[0] == '\0') {
            continue;
        }
        if (g_strcmp0(display_name, ".") == 0) {
            continue;
        }

        if (g_strcmp0(display_name, "..") == 0) {
            full_path = g_path_get_dirname(path);
        } else {
            full_path = g_strdup_printf("%s/%s", path, display_name);
        }

        gtk_list_box_append(
            GTK_LIST_BOX(ui->explorer_list_box),
            create_explorer_row(display_name, full_path, is_dir)
        );
        count++;
    }

    if (count == 0) {
        GtkWidget *row = gtk_list_box_row_new();
        GtkWidget *label = gtk_label_new("No visible entries in this directory.");
        gtk_widget_add_css_class(label, "panel-subtitle");
        gtk_label_set_xalign(GTK_LABEL(label), 0.0f);
        gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(row), label);
        gtk_list_box_append(GTK_LIST_BOX(ui->explorer_list_box), row);
    }
}

static void
render_session_snapshot(LiaraWindow *ui, const char *response_text)
{
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonObject *root;
    JsonObject *metadata = NULL;
    const char *history_status = NULL;
    const char *last_run_id = NULL;
    const char *updated_at = NULL;
    const char *sandbox_root = NULL;
    gint64 message_count = 0;

    set_snapshot_int_value(ui->session_snapshot_count, "Messages: ", 0);
    set_snapshot_value(ui->session_snapshot_last_run, "Last run: ", NULL);
    set_snapshot_value(ui->session_snapshot_updated, "Updated: ", NULL);
    set_snapshot_value(ui->session_snapshot_history, "History: ", NULL);
    set_snapshot_value(ui->session_snapshot_sandbox, "Sandbox root: ", NULL);
    if (ui->session_meta_profile_entry != NULL) {
        gtk_editable_set_text(GTK_EDITABLE(ui->session_meta_profile_entry), "");
    }
    if (ui->session_meta_workspace_entry != NULL) {
        gtk_editable_set_text(GTK_EDITABLE(ui->session_meta_workspace_entry), "");
    }
    if (ui->session_meta_notes_entry != NULL) {
        gtk_editable_set_text(GTK_EDITABLE(ui->session_meta_notes_entry), "");
    }

    if (response_text == NULL || !json_parser_load_from_data(parser, response_text, -1, NULL)) {
        return;
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL) {
        return;
    }

    if (json_object_has_member(root, "message_count")) {
        message_count = json_object_get_int_member(root, "message_count");
    }
    if (json_object_has_member(root, "last_run_id")) {
        last_run_id = json_object_get_string_member(root, "last_run_id");
    }
    if (json_object_has_member(root, "updated_at")) {
        updated_at = json_object_get_string_member(root, "updated_at");
    }
    if (json_object_has_member(root, "metadata")) {
        metadata = json_object_get_object_member(root, "metadata");
    }
    if (metadata != NULL) {
        if (json_object_has_member(metadata, "history_status")) {
            history_status = json_object_get_string_member(metadata, "history_status");
        }
        if (json_object_has_member(metadata, "sandbox_root")) {
            sandbox_root = json_object_get_string_member(metadata, "sandbox_root");
        }
    }

    set_snapshot_int_value(ui->session_snapshot_count, "Messages: ", message_count);
    set_snapshot_value(ui->session_snapshot_last_run, "Last run: ", last_run_id);
    set_snapshot_value(ui->session_snapshot_updated, "Updated: ", updated_at);
    set_snapshot_value(ui->session_snapshot_history, "History: ", history_status);
    set_snapshot_value(ui->session_snapshot_sandbox, "Sandbox root: ", sandbox_root);

    if (metadata != NULL) {
        const char *profile = json_object_has_member(metadata, "profile") ? json_object_get_string_member(metadata, "profile") : "";
        const char *workspace = json_object_has_member(metadata, "workspace") ? json_object_get_string_member(metadata, "workspace") : "";
        const char *notes = json_object_has_member(metadata, "notes") ? json_object_get_string_member(metadata, "notes") : "";
        if (ui->session_meta_profile_entry != NULL) {
            gtk_editable_set_text(GTK_EDITABLE(ui->session_meta_profile_entry), profile != NULL ? profile : "");
        }
        if (ui->session_meta_workspace_entry != NULL) {
            gtk_editable_set_text(GTK_EDITABLE(ui->session_meta_workspace_entry), workspace != NULL ? workspace : "");
        }
        if (ui->session_meta_notes_entry != NULL) {
            gtk_editable_set_text(GTK_EDITABLE(ui->session_meta_notes_entry), notes != NULL ? notes : "");
        }
    }
}

static gboolean
append_parsed_json_value(JsonBuilder *builder, const char *raw_value)
{
    g_autoptr(JsonParser) parser = NULL;
    JsonNode *root = NULL;

    if (raw_value == NULL || raw_value[0] == '\0') {
        return FALSE;
    }

    parser = json_parser_new();
    if (!json_parser_load_from_data(parser, raw_value, -1, NULL)) {
        return FALSE;
    }

    root = json_parser_get_root(parser);
    if (root == NULL) {
        return FALSE;
    }

    json_builder_add_value(builder, json_node_copy(root));
    return TRUE;
}

static char *
build_session_metadata_json(LiaraWindow *ui)
{
    g_autoptr(JsonBuilder) builder = json_builder_new();
    g_autoptr(JsonGenerator) generator = json_generator_new();
    g_autoptr(JsonNode) root = NULL;
    const char *profile = gtk_editable_get_text(GTK_EDITABLE(ui->session_meta_profile_entry));
    const char *workspace = gtk_editable_get_text(GTK_EDITABLE(ui->session_meta_workspace_entry));
    const char *notes = gtk_editable_get_text(GTK_EDITABLE(ui->session_meta_notes_entry));
    const char *display_name = ui->identity_display_name != NULL
        ? gtk_label_get_text(ui->identity_display_name)
        : NULL;
    const char *login_name = ui->identity_login_name != NULL
        ? gtk_label_get_text(ui->identity_login_name)
        : NULL;

    json_builder_begin_object(builder);
    if (profile != NULL && profile[0] != '\0') {
        json_builder_set_member_name(builder, "profile");
        json_builder_add_string_value(builder, profile);
    }
    if (workspace != NULL && workspace[0] != '\0') {
        json_builder_set_member_name(builder, "workspace");
        json_builder_add_string_value(builder, workspace);
    }
    if (notes != NULL && notes[0] != '\0') {
        json_builder_set_member_name(builder, "notes");
        json_builder_add_string_value(builder, notes);
    }
    if (display_name != NULL && display_name[0] != '\0') {
        json_builder_set_member_name(builder, "display_name");
        json_builder_add_string_value(builder, display_name);
    }
    if (login_name != NULL && login_name[0] != '\0') {
        json_builder_set_member_name(builder, "login_name");
        json_builder_add_string_value(builder, login_name);
    }
    json_builder_end_object(builder);

    root = json_builder_get_root(builder);
    json_generator_set_root(generator, root);
    return json_generator_to_data(generator, NULL);
}

static GtkWidget *
create_tool_form_row(const char *param_name, gboolean required)
{
    GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *label = gtk_label_new(param_name);
    GtkWidget *entry = gtk_entry_new();

    gtk_widget_add_css_class(row, "tool-form-row");
    gtk_widget_add_css_class(entry, "tool-form-entry");
    gtk_label_set_xalign(GTK_LABEL(label), 0.0f);
    gtk_widget_set_hexpand(entry, TRUE);
    gtk_editable_set_text(GTK_EDITABLE(entry), "");
    gtk_entry_set_placeholder_text(
        GTK_ENTRY(entry),
        required ? "required value" : "optional value"
    );

    g_object_set_data_full(G_OBJECT(row), "tool-param-name", g_strdup(param_name), g_free);
    g_object_set_data(G_OBJECT(row), "tool-param-entry", entry);

    gtk_box_append(GTK_BOX(row), label);
    gtk_box_append(GTK_BOX(row), entry);
    return row;
}

static void
populate_tool_form_from_array(GtkWidget *box, JsonArray *params, gboolean required)
{
    guint i;

    if (params == NULL) {
        return;
    }

    for (i = 0; i < json_array_get_length(params); i++) {
        const char *param = json_array_get_string_element(params, i);
        if (param != NULL && param[0] != '\0') {
            gtk_box_append(GTK_BOX(box), create_tool_form_row(param, required));
        }
    }
}

static void
populate_dynamic_tool_form(LiaraWindow *ui, JsonArray *required, JsonArray *optional)
{
    clear_box_children(ui->tool_form_box);
    populate_tool_form_from_array(ui->tool_form_box, required, TRUE);
    populate_tool_form_from_array(ui->tool_form_box, optional, FALSE);

    if (gtk_widget_get_first_child(ui->tool_form_box) == NULL) {
        gtk_box_append(GTK_BOX(ui->tool_form_box), create_metadata_chip("This tool does not need parameter inputs."));
    }
}

static char *
build_tool_parameters_json(LiaraWindow *ui)
{
    g_autoptr(JsonBuilder) builder = json_builder_new();
    g_autoptr(JsonGenerator) generator = json_generator_new();
    g_autoptr(JsonNode) root = NULL;
    GtkWidget *child = gtk_widget_get_first_child(ui->tool_form_box);
    gboolean has_form_values = FALSE;

    json_builder_begin_object(builder);

    while (child != NULL) {
        const char *param_name = g_object_get_data(G_OBJECT(child), "tool-param-name");
        GtkWidget *entry = g_object_get_data(G_OBJECT(child), "tool-param-entry");

        if (param_name != NULL && entry != NULL) {
            const char *value = gtk_editable_get_text(GTK_EDITABLE(entry));
            if (value != NULL && value[0] != '\0') {
                has_form_values = TRUE;
                json_builder_set_member_name(builder, param_name);
                if (!append_parsed_json_value(builder, value)) {
                    json_builder_add_string_value(builder, value);
                }
            }
        }

        child = gtk_widget_get_next_sibling(child);
    }

    json_builder_end_object(builder);

    if (!has_form_values) {
        return get_text_view_text(ui->tool_params_input);
    }

    root = json_builder_get_root(builder);
    json_generator_set_root(generator, root);
    return json_generator_to_data(generator, NULL);
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
    gtk_scrolled_window_set_propagate_natural_width(GTK_SCROLLED_WINDOW(scroller), FALSE);
    gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(scroller), FALSE);
    gtk_widget_set_hexpand(scroller, TRUE);
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
        g_autofree char *bullet = g_strdup_printf("- %s", line + 2);
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
    /* Use the new formatted text renderer with copy buttons and syntax highlighting */
    GtkWidget *formatted = liara_create_formatted_text(text);
    
    clear_box_children(content_box);
    gtk_box_append(GTK_BOX(content_box), formatted);
}

static GtkWidget *
create_tool_trace_revealer(const char *tool_name, const char *memory_tier, const char *judge_decision)
{
    /* Expandable section for tool trace metadata */
    GtkWidget *expander = gtk_expander_new("Details");
    GtkWidget *trace_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
    
    gtk_widget_add_css_class(expander, "chat-trace-expander");
    gtk_widget_add_css_class(trace_box, "chat-trace-content");
    gtk_widget_set_margin_top(trace_box, 4);
    gtk_widget_set_margin_bottom(trace_box, 4);
    gtk_widget_set_margin_start(trace_box, 6);
    gtk_widget_set_margin_end(trace_box, 6);
    
    /* Tool info */
    if (tool_name != NULL && tool_name[0] != '\0') {
        GtkWidget *tool_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
        GtkWidget *tool_label = gtk_label_new("Tool:");
        GtkWidget *tool_value = gtk_label_new(tool_name);
        
        gtk_widget_add_css_class(tool_label, "chat-trace-label");
        gtk_widget_add_css_class(tool_value, "chat-trace-value");
        gtk_label_set_xalign(GTK_LABEL(tool_label), 0.0f);
        gtk_label_set_xalign(GTK_LABEL(tool_value), 0.0f);
        
        gtk_box_append(GTK_BOX(tool_row), tool_label);
        gtk_box_append(GTK_BOX(tool_row), tool_value);
        gtk_box_append(GTK_BOX(trace_box), tool_row);
    }
    
    /* Memory tier info */
    if (memory_tier != NULL && memory_tier[0] != '\0') {
        GtkWidget *memory_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
        GtkWidget *memory_label = gtk_label_new("Memory:");
        GtkWidget *memory_value = gtk_label_new(memory_tier);
        
        gtk_widget_add_css_class(memory_label, "chat-trace-label");
        gtk_widget_add_css_class(memory_value, "chat-trace-value");
        gtk_label_set_xalign(GTK_LABEL(memory_label), 0.0f);
        gtk_label_set_xalign(GTK_LABEL(memory_value), 0.0f);
        
        gtk_box_append(GTK_BOX(memory_row), memory_label);
        gtk_box_append(GTK_BOX(memory_row), memory_value);
        gtk_box_append(GTK_BOX(trace_box), memory_row);
    }
    
    /* Judge decision info */
    if (judge_decision != NULL && judge_decision[0] != '\0') {
        GtkWidget *judge_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
        GtkWidget *judge_label = gtk_label_new("Judge:");
        GtkWidget *judge_value = gtk_label_new(judge_decision);
        
        gtk_widget_add_css_class(judge_label, "chat-trace-label");
        gtk_widget_add_css_class(judge_value, "chat-trace-value");
        gtk_label_set_xalign(GTK_LABEL(judge_label), 0.0f);
        gtk_label_set_xalign(GTK_LABEL(judge_value), 0.0f);
        
        gtk_box_append(GTK_BOX(judge_row), judge_label);
        gtk_box_append(GTK_BOX(judge_row), judge_value);
        gtk_box_append(GTK_BOX(trace_box), judge_row);
    }
    
    gtk_expander_set_child(GTK_EXPANDER(expander), trace_box);
    return expander;
}

typedef struct {
    GtkWidget *revealer;
    GtkWidget *toggle_button;
    GtkWidget *panel_box;
    gboolean is_tool_panel;
} DevPanelState;

static void
on_dev_toggle_clicked(GtkButton *button, gpointer user_data)
{
    DevPanelState *state = (DevPanelState *) user_data;
    gboolean revealed = gtk_revealer_get_reveal_child(GTK_REVEALER(state->revealer));
    
    gtk_revealer_set_reveal_child(GTK_REVEALER(state->revealer), !revealed);
    gtk_button_set_label(button, !revealed ? "Details ▲" : "Details ▼");
}

static DevPanelState *
dev_panel_state_new(void)
{
    return g_new0(DevPanelState, 1);
}

static void
dev_panel_state_free(DevPanelState *state)
{
    if (state != NULL) {
        g_free(state);
    }
}

static GtkWidget *
create_dev_panel_with_metadata(const char **keys, const char **values, gint count, gboolean is_tool)
{
    /* 
     * Create a togglable dev panel with key-value metadata.
     * Returns a GtkBox containing [toggle button] [revealer with details]
     */
    GtkWidget *container = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
    GtkWidget *toggle_btn = gtk_button_new_with_label("Details ▼");
    GtkWidget *revealer = gtk_revealer_new();
    GtkWidget *panel_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    
    DevPanelState *state = dev_panel_state_new();
    state->revealer = revealer;
    state->toggle_button = toggle_btn;
    state->panel_box = panel_box;
    state->is_tool_panel = is_tool;
    
    /* Style toggle button */
    gtk_widget_add_css_class(toggle_btn, "dev-toggle");
    gtk_widget_set_margin_top(toggle_btn, 4);
    gtk_widget_set_margin_bottom(toggle_btn, 0);
    gtk_widget_set_margin_start(toggle_btn, 0);
    gtk_widget_set_margin_end(toggle_btn, 0);
    gtk_button_set_has_frame(GTK_BUTTON(toggle_btn), FALSE);
    
    /* Style dev panel */
    gtk_widget_add_css_class(panel_box, "dev-panel");
    if (is_tool) {
        gtk_widget_add_css_class(panel_box, "dev-panel-tool");
    }
    gtk_widget_set_margin_top(panel_box, 4);
    gtk_widget_set_margin_start(panel_box, 2);
    gtk_widget_set_margin_end(panel_box, 2);
    gtk_widget_set_margin_bottom(panel_box, 4);
    
    /* Add metadata rows */
    for (gint i = 0; i < count && keys[i] != NULL; i++) {
        GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
        GtkWidget *key_label = gtk_label_new(keys[i]);
        GtkWidget *val_label = gtk_label_new(values[i] != NULL ? values[i] : "");
        
        gtk_widget_add_css_class(key_label, "dev-meta-key");
        gtk_widget_add_css_class(val_label, "dev-meta-value");
        
        gtk_label_set_xalign(GTK_LABEL(key_label), 0.0f);
        gtk_label_set_xalign(GTK_LABEL(val_label), 0.0f);
        gtk_label_set_selectable(GTK_LABEL(val_label), TRUE);
        
        gtk_label_set_max_width_chars(GTK_LABEL(val_label), 50);
        gtk_label_set_ellipsize(GTK_LABEL(val_label), PANGO_ELLIPSIZE_END);
        
        gtk_box_append(GTK_BOX(row), key_label);
        gtk_box_append(GTK_BOX(row), val_label);
        gtk_box_append(GTK_BOX(panel_box), row);
    }
    
    gtk_revealer_set_child(GTK_REVEALER(revealer), panel_box);
    gtk_revealer_set_reveal_child(GTK_REVEALER(revealer), FALSE);
    gtk_revealer_set_transition_type(GTK_REVEALER(revealer), GTK_REVEALER_TRANSITION_TYPE_SLIDE_DOWN);
    gtk_revealer_set_transition_duration(GTK_REVEALER(revealer), 200);
    
    /* Connect toggle button signal */
    g_signal_connect(toggle_btn, "clicked", G_CALLBACK(on_dev_toggle_clicked), state);
    g_object_set_data_full(G_OBJECT(toggle_btn), "dev-panel-state", state, (GDestroyNotify) dev_panel_state_free);
    
    /* Assemble container */
    gtk_box_append(GTK_BOX(container), toggle_btn);
    gtk_box_append(GTK_BOX(container), revealer);
    
    gtk_widget_add_css_class(container, "dev-panel-container");
    
    return container;
}

static GtkWidget *
append_chat_message(
    LiaraWindow *ui,
    const char *role,
    const char *text,
    gboolean assistant,
    GtkLabel **stream_label_out,
    GtkWidget **meta_box_out)
{
    /* Create ListBoxRow for chat stream */
    GtkWidget *list_row = gtk_list_box_row_new();
    gtk_list_box_row_set_selectable(GTK_LIST_BOX_ROW(list_row), FALSE);
    gtk_list_box_row_set_activatable(GTK_LIST_BOX_ROW(list_row), FALSE);

    /* Horizontal padding row: [spacer] bubble [spacer] */
    GtkWidget *row_container = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 12);
    gtk_widget_add_css_class(row_container, "chat-row-container");
    gtk_widget_set_hexpand(row_container, TRUE);
    gtk_widget_set_margin_top(row_container, 8);
    gtk_widget_set_margin_bottom(row_container, 8);
    gtk_widget_set_margin_start(row_container, 12);
    gtk_widget_set_margin_end(row_container, 12);

    /* Bubble: vertical box with role, content, metadata */
    GtkWidget *bubble = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    gtk_widget_add_css_class(bubble, "chat-bubble");
    gtk_widget_add_css_class(bubble, assistant ? "chat-bubble-assistant" : "chat-bubble-user");
    gtk_widget_set_margin_top(bubble, 4);
    gtk_widget_set_margin_bottom(bubble, 4);
    gtk_widget_set_margin_start(bubble, 8);
    gtk_widget_set_margin_end(bubble, 8);

    /* Role label */
    GtkWidget *role_label = gtk_label_new(role);
    gtk_widget_add_css_class(role_label, "chat-bubble-role");
    gtk_label_set_xalign(GTK_LABEL(role_label), 0.0f);

    /* Content container */
    GtkWidget *content_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    gtk_widget_add_css_class(content_box, "chat-bubble-content");
    GtkWidget *content_label = NULL;

    /* Metadata box (for tool traces, etc.) */
    GtkWidget *meta_box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    gtk_widget_add_css_class(meta_box, "chat-bubble-meta");

    /* Assemble bubble */
    gtk_box_append(GTK_BOX(bubble), role_label);
    gtk_box_append(GTK_BOX(bubble), content_box);
    gtk_box_append(GTK_BOX(bubble), meta_box);

    /* Add spacers for positioning: assistant left, user right */
    if (assistant) {
        /* Assistant: bubble on left */
        gtk_box_append(GTK_BOX(row_container), bubble);
        GtkWidget *right_spacer = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
        gtk_widget_set_hexpand(right_spacer, TRUE);
        gtk_box_append(GTK_BOX(row_container), right_spacer);
    } else {
        /* User: bubble on right */
        GtkWidget *left_spacer = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
        gtk_widget_set_hexpand(left_spacer, TRUE);
        gtk_box_append(GTK_BOX(row_container), left_spacer);
        gtk_box_append(GTK_BOX(row_container), bubble);
    }

    gtk_widget_set_halign(bubble, assistant ? GTK_ALIGN_START : GTK_ALIGN_END);
    gtk_widget_set_hexpand(bubble, FALSE);

    /* Add row to ListBox */
    gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(list_row), row_container);
    gtk_list_box_append(GTK_LIST_BOX(ui->chat_messages_box), list_row);

    if (stream_label_out != NULL) {
        content_label = gtk_label_new(text != NULL ? text : "");
        gtk_widget_add_css_class(content_label, "chat-bubble-text");
        gtk_label_set_xalign(GTK_LABEL(content_label), 0.0f);
        gtk_label_set_wrap(GTK_LABEL(content_label), TRUE);
        gtk_label_set_wrap_mode(GTK_LABEL(content_label), PANGO_WRAP_WORD_CHAR);
        gtk_label_set_selectable(GTK_LABEL(content_label), TRUE);
        gtk_label_set_max_width_chars(GTK_LABEL(content_label), 60);
        gtk_box_append(GTK_BOX(content_box), content_label);
        *stream_label_out = GTK_LABEL(content_label);
        g_message("[BUBBLE] Created bubble with stream label for role='%s'", role);
    } else {
        render_message_content(content_box, text, assistant);
        g_message("[BUBBLE] Created bubble with rendered content for role='%s'", role);
    }

    if (meta_box_out != NULL) {
        *meta_box_out = meta_box;
    }

    gtk_widget_set_visible(meta_box, FALSE);

    scroll_chat_to_bottom(ui);
    return content_box;
}

static const char *
format_message_role_label(const char *role, gboolean assistant)
{
    if (role == NULL || role[0] == '\0') {
        return assistant ? "LIARA" : "You";
    }

    if (g_ascii_strcasecmp(role, "assistant") == 0) {
        return "LIARA";
    }

    if (g_ascii_strcasecmp(role, "user") == 0) {
        return "You";
    }

    if (g_ascii_strcasecmp(role, "tool") == 0) {
        return "Tool";
    }

    return role;
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

static const char *
json_object_get_string_or(JsonObject *object, const char *member_name, const char *fallback)
{
    if (object == NULL || !json_object_has_member(object, member_name)) {
        return fallback;
    }
    if (!JSON_NODE_HOLDS_VALUE(json_object_get_member(object, member_name))) {
        return fallback;
    }
    return json_object_get_string_member(object, member_name);
}

static int
json_object_get_int_or(JsonObject *object, const char *member_name, int fallback)
{
    if (object == NULL || !json_object_has_member(object, member_name)) {
        return fallback;
    }
    return (int) json_object_get_int_member(object, member_name);
}

static double
json_object_get_double_or(JsonObject *object, const char *member_name, double fallback)
{
    if (object == NULL || !json_object_has_member(object, member_name)) {
        return fallback;
    }
    return json_object_get_double_member(object, member_name);
}

static void
append_filter_summary(GString *out, JsonObject *filters)
{
    const char *source;
    const char *risk;
    const char *family;
    const char *blocked;
    int limit;
    int max_items;

    if (filters == NULL) {
        return;
    }

    source = json_object_get_string_or(filters, "source", "all");
    risk = json_object_get_string_or(filters, "risk_level", "all");
    family = json_object_get_string_or(filters, "command_family", "all");
    blocked = (json_object_has_member(filters, "blocked_only") && json_object_get_boolean_member(filters, "blocked_only"))
        ? "yes"
        : "no";
    limit = json_object_get_int_or(filters, "limit", 0);
    max_items = json_object_get_int_or(filters, "max_items", 0);

    g_string_append(out, "Filters\n");
    g_string_append_printf(out, "- source: %s\n", source);
    g_string_append_printf(out, "- risk: %s\n", risk);
    g_string_append_printf(out, "- family: %s\n", family);
    g_string_append_printf(out, "- blocked_only: %s\n", blocked);
    if (limit > 0) {
        g_string_append_printf(out, "- limit: %d\n", limit);
    }
    if (max_items > 0) {
        g_string_append_printf(out, "- max_items: %d\n", max_items);
    }
    g_string_append(out, "\n");
}

static void
append_top_tuples(GString *out, JsonObject *summary, const char *member_name, const char *title)
{
    JsonArray *entries;
    guint i;

    if (summary == NULL || !json_object_has_member(summary, member_name)) {
        return;
    }
    entries = json_object_get_array_member(summary, member_name);
    if (entries == NULL || json_array_get_length(entries) == 0) {
        return;
    }

    g_string_append_printf(out, "%s\n", title);
    for (i = 0; i < json_array_get_length(entries); i++) {
        JsonArray *tuple;
        const char *label = "unknown";
        int count = 0;
        JsonNode *node = json_array_get_element(entries, i);
        if (node == NULL || !JSON_NODE_HOLDS_ARRAY(node)) {
            continue;
        }
        tuple = json_node_get_array(node);
        if (tuple == NULL || json_array_get_length(tuple) < 2) {
            continue;
        }
        label = json_array_get_string_element(tuple, 0);
        count = (int) json_array_get_int_element(tuple, 1);
        g_string_append_printf(out, "- %s: %d\n", label != NULL ? label : "unknown", count);
    }
    g_string_append(out, "\n");
}

static void
append_suspicious_items(GString *out, JsonArray *items)
{
    guint i;
    guint total;
    guint to_show;

    if (items == NULL) {
        return;
    }

    total = json_array_get_length(items);
    to_show = MIN(total, 12);
    g_string_append_printf(out, "Suspicious Events (showing %u of %u)\n", to_show, total);

    for (i = 0; i < to_show; i++) {
        JsonObject *item;
        const char *command;
        const char *source;
        const char *risk;
        const char *decision;
        const char *context;
        const char *reason;
        int exit_code;
        double duration_ms;

        item = json_array_get_object_element(items, i);
        if (item == NULL) {
            continue;
        }

        command = json_object_get_string_or(item, "command", "unknown");
        source = json_object_get_string_or(item, "source", "unknown");
        risk = json_object_get_string_or(item, "risk_level", "unknown");
        decision = json_object_get_string_or(item, "policy_decision", "unknown");
        context = json_object_get_string_or(item, "context", "-");
        reason = json_object_get_string_or(item, "policy_reason", "-");
        exit_code = json_object_get_int_or(item, "exit_code", 0);
        duration_ms = json_object_get_double_or(item, "duration_ms", 0.0);

        g_string_append_printf(out, "%u) %s\n", i + 1, command);
        g_string_append_printf(out, "   source=%s  risk=%s  decision=%s  exit=%d\n", source, risk, decision, exit_code);
        g_string_append_printf(out, "   duration=%.1f ms\n", duration_ms);
        if (g_strcmp0(reason, "-") != 0) {
            g_string_append_printf(out, "   reason=%s\n", reason);
        }
        g_string_append_printf(out, "   context=%s\n", context);
    }
    g_string_append(out, "\n");
}

static char *
format_audit_response_for_humans(const char *response_text)
{
    g_autoptr(JsonParser) parser = json_parser_new();
    g_autoptr(GString) out = g_string_new(NULL);
    JsonObject *root;
    JsonObject *summary;
    JsonObject *filters;
    JsonObject *config;
    JsonArray *items;
    const char *status;
    const char *preset;

    if (response_text == NULL || response_text[0] == '\0') {
        return g_strdup("No audit data received.");
    }
    if (!json_parser_load_from_data(parser, response_text, -1, NULL)) {
        return g_strdup(response_text);
    }
    if (!JSON_NODE_HOLDS_OBJECT(json_parser_get_root(parser))) {
        return g_strdup(response_text);
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL) {
        return g_strdup(response_text);
    }

    status = json_object_get_string_or(root, "status", "unknown");
    g_string_append(out, "Sys Audit Overview\n");
    g_string_append_printf(out, "Status: %s\n\n", status);

    if (json_object_has_member(root, "preset")) {
        preset = json_object_get_string_or(root, "preset", "unknown");
        g_string_append_printf(out, "Preset: %s\n\n", preset);
    }

    if (json_object_has_member(root, "filters")) {
        filters = json_object_get_object_member(root, "filters");
        append_filter_summary(out, filters);
    } else if (json_object_has_member(root, "config")) {
        config = json_object_get_object_member(root, "config");
        append_filter_summary(out, config);
    }

    if (json_object_has_member(root, "summary")) {
        summary = json_object_get_object_member(root, "summary");
        if (summary != NULL) {
            g_string_append(out, "Key Metrics\n");
            g_string_append_printf(out, "- total: %d\n", json_object_get_int_or(summary, "total", 0));
            g_string_append_printf(out, "- filtered_entries: %d\n", json_object_get_int_or(summary, "filtered_entries", 0));
            g_string_append_printf(out, "- inspected_entries: %d\n", json_object_get_int_or(summary, "inspected_entries", 0));
            g_string_append_printf(out, "- allowed: %d\n", json_object_get_int_or(summary, "allowed", 0));
            g_string_append_printf(out, "- blocked: %d\n", json_object_get_int_or(summary, "blocked", 0));
            g_string_append_printf(out, "- failed_allowed: %d\n", json_object_get_int_or(summary, "failed_allowed", 0));
            g_string_append_printf(out, "- high_risk: %d\n", json_object_get_int_or(summary, "high_risk", 0));
            g_string_append_printf(out, "- network_calls: %d\n", json_object_get_int_or(summary, "network_calls", 0));
            g_string_append_printf(out, "- write_ops: %d\n", json_object_get_int_or(summary, "write_ops", 0));
            if (json_object_has_member(summary, "avg_duration_ms")) {
                g_string_append_printf(out, "- avg_duration_ms: %.1f\n", json_object_get_double_or(summary, "avg_duration_ms", 0.0));
            }
            g_string_append(out, "\n");

            append_top_tuples(out, summary, "top_sources", "Top Sources");
            append_top_tuples(out, summary, "top_contexts", "Top Contexts");
        }
    }

    if (json_object_has_member(root, "count")) {
        g_string_append_printf(out, "Suspicious Count: %d\n\n", json_object_get_int_or(root, "count", 0));
    }

    if (json_object_has_member(root, "items")) {
        items = json_object_get_array_member(root, "items");
        append_suspicious_items(out, items);
    }

    if (out->len == 0) {
        return g_strdup(response_text);
    }
    return g_string_free(g_steal_pointer(&out), FALSE);
}

static void
on_audit_response_loaded(const char *response_text, GError *error, gpointer user_data)
{
    GtkTextView *view = GTK_TEXT_VIEW(user_data);

    if (error != NULL) {
        g_autofree char *message = g_strdup_printf("Audit request failed\n\n%s", error->message);
        set_text_view_text(view, message);
        g_error_free(error);
        return;
    }

    {
        g_autofree char *formatted = format_audit_response_for_humans(response_text);
        set_text_view_text(view, formatted);
    }
}

static GtkWidget *
create_health_card(const char *backend, const char *state)
{
    GtkWidget *card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    GtkWidget *name_label = gtk_label_new(backend);
    GtkWidget *state_label = gtk_label_new(state);

    gtk_widget_add_css_class(card, "health-card");
    if (g_strcmp0(state, "healthy") == 0 || g_strcmp0(state, "ok") == 0) {
        gtk_widget_add_css_class(card, "health-card-healthy");
    } else if (g_strcmp0(state, "degraded") == 0 || g_strcmp0(state, "partial") == 0) {
        gtk_widget_add_css_class(card, "health-card-degraded");
    } else {
        gtk_widget_add_css_class(card, "health-card-unavailable");
    }

    gtk_widget_add_css_class(name_label, "health-card-name");
    gtk_widget_add_css_class(state_label, "health-card-state");
    gtk_label_set_xalign(GTK_LABEL(name_label), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(state_label), 0.0f);

    gtk_box_append(GTK_BOX(card), name_label);
    gtk_box_append(GTK_BOX(card), state_label);
    return card;
}

static void
render_health_cards(GtkWidget *container, GtkLabel *summary_label, const char *response_text, gboolean backend_mode)
{
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonObject *root;
    guint healthy_count = 0;
    guint degraded_count = 0;
    guint unavailable_count = 0;

    clear_box_children(container);
    gtk_label_set_text(summary_label, "No health data loaded yet.");

    if (response_text == NULL || !json_parser_load_from_data(parser, response_text, -1, NULL)) {
        gtk_box_append(GTK_BOX(container), create_health_card("health", "unavailable"));
        gtk_label_set_text(summary_label, "Health response could not be parsed.");
        return;
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL) {
        gtk_box_append(GTK_BOX(container), create_health_card("health", "unavailable"));
        gtk_label_set_text(summary_label, "Health response was empty.");
        return;
    }

    if (backend_mode) {
        JsonObject *status_object = NULL;
        JsonObject *backend_health = NULL;
        GList *members;
        GList *iter;

        if (json_object_has_member(root, "status")) {
            status_object = json_object_get_object_member(root, "status");
            if (status_object != NULL && json_object_has_member(status_object, "status")) {
                const char *service_status = json_object_get_string_member(status_object, "status");
                gtk_box_append(GTK_BOX(container), create_health_card("memory-service", service_status));
            }
        }

        if (!json_object_has_member(root, "backend_health")) {
            gtk_label_set_text(summary_label, "Backend health did not contain backend_health.");
            return;
        }

        backend_health = json_object_get_object_member(root, "backend_health");
        members = json_object_get_members(backend_health);
        for (iter = members; iter != NULL; iter = iter->next) {
            const char *backend = iter->data;
            const char *state = json_object_get_string_member(backend_health, backend);

            if (g_strcmp0(state, "healthy") == 0) {
                healthy_count++;
            } else if (g_strcmp0(state, "degraded") == 0) {
                degraded_count++;
            } else {
                unavailable_count++;
            }

            gtk_box_append(GTK_BOX(container), create_health_card(backend, state));
        }
        g_list_free(members);
    } else {
        JsonObject *configured = NULL;
        const char *service_status = json_object_has_member(root, "status")
            ? json_object_get_string_member(root, "status")
            : "unknown";
        const char *memory_mode = json_object_has_member(root, "memory_mode")
            ? json_object_get_string_member(root, "memory_mode")
            : "unknown";

        gtk_box_append(GTK_BOX(container), create_health_card("api", service_status));
        gtk_box_append(GTK_BOX(container), create_health_card("memory-mode", memory_mode));

        if (json_object_has_member(root, "backends_configured")) {
            GList *members;
            GList *iter;

            configured = json_object_get_object_member(root, "backends_configured");
            members = json_object_get_members(configured);
            for (iter = members; iter != NULL; iter = iter->next) {
                const char *backend = iter->data;
                const char *state = json_object_get_boolean_member(configured, backend) ? "configured" : "off";

                if (json_object_get_boolean_member(configured, backend)) {
                    healthy_count++;
                } else {
                    unavailable_count++;
                }

                gtk_box_append(GTK_BOX(container), create_health_card(backend, state));
            }
            g_list_free(members);
        }
    }

    {
        g_autofree char *summary = g_strdup_printf(
            "%u healthy/configured, %u degraded, %u unavailable/off",
            healthy_count,
            degraded_count,
            unavailable_count
        );
        gtk_label_set_text(summary_label, summary);
    }
}

static GtkWidget *
append_history_message(LiaraWindow *ui, const char *role, const char *text)
{
    GtkWidget *list_row = gtk_list_box_row_new();
    GtkWidget *row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 12);
    GtkWidget *bubble = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    GtkWidget *role_label;
    GtkWidget *content_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    gboolean assistant = g_strcmp0(role, "assistant") == 0 || g_strcmp0(role, "tool") == 0;

    role_label = gtk_label_new(format_message_role_label(role, assistant));

    gtk_list_box_row_set_selectable(GTK_LIST_BOX_ROW(list_row), FALSE);
    gtk_list_box_row_set_activatable(GTK_LIST_BOX_ROW(list_row), FALSE);
    gtk_widget_add_css_class(row, "chat-row-container");
    gtk_widget_add_css_class(bubble, "chat-bubble");
    gtk_widget_add_css_class(bubble, assistant ? "chat-bubble-assistant" : "chat-bubble-user");
    gtk_widget_add_css_class(role_label, "chat-bubble-role");
    gtk_widget_add_css_class(content_box, "chat-bubble-content");

    gtk_widget_set_hexpand(row, TRUE);
    gtk_widget_set_margin_top(row, 8);
    gtk_widget_set_margin_bottom(row, 8);
    gtk_widget_set_margin_start(row, 12);
    gtk_widget_set_margin_end(row, 12);
    gtk_widget_set_margin_top(bubble, 4);
    gtk_widget_set_margin_bottom(bubble, 4);
    gtk_widget_set_margin_start(bubble, 8);
    gtk_widget_set_margin_end(bubble, 8);
    gtk_label_set_xalign(GTK_LABEL(role_label), 0.0f);

    gtk_box_append(GTK_BOX(bubble), role_label);
    gtk_box_append(GTK_BOX(bubble), content_box);

    if (assistant) {
        GtkWidget *right_spacer = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
        gtk_widget_set_hexpand(right_spacer, TRUE);
        gtk_box_append(GTK_BOX(row), bubble);
        gtk_box_append(GTK_BOX(row), right_spacer);
    } else {
        GtkWidget *left_spacer = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
        gtk_widget_set_hexpand(left_spacer, TRUE);
        gtk_box_append(GTK_BOX(row), left_spacer);
        gtk_box_append(GTK_BOX(row), bubble);
    }

    gtk_widget_set_halign(bubble, assistant ? GTK_ALIGN_START : GTK_ALIGN_END);
    gtk_widget_set_hexpand(bubble, FALSE);
    gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(list_row), row);
    gtk_list_box_append(GTK_LIST_BOX(ui->history_messages_box), list_row);

    render_message_content(content_box, text != NULL ? text : "", assistant);
    scroll_history_to_bottom(ui);
    return content_box;
}

static GtkWidget *
create_metadata_chip(const char *text)
{
    GtkWidget *label = gtk_label_new(text);

    gtk_widget_add_css_class(label, "meta-chip");
    gtk_label_set_xalign(GTK_LABEL(label), 0.0f);
    return label;
}

static void
append_metadata_chip(GtkWidget *meta_box, const char *text)
{
    if (meta_box == NULL || text == NULL || text[0] == '\0') {
        return;
    }

    gtk_box_append(GTK_BOX(meta_box), create_metadata_chip(text));
}

static void
render_assistant_metadata(GtkWidget *meta_box, const char *final_payload)
{
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonObject *root;
    JsonArray *tools = NULL;
    const char *provider = NULL;
    const char *model = NULL;
    const char *run_id = NULL;
    const char *context_mode = NULL;
    gboolean validation_known = FALSE;
    gboolean validation_passed = FALSE;
    gint64 ttft_ms = -1;
    gint64 gen_ms = -1;

    if (meta_box == NULL) {
        return;
    }

    clear_box_children(meta_box);
    gtk_widget_set_visible(meta_box, FALSE);

    if (final_payload == NULL || !json_parser_load_from_data(parser, final_payload, -1, NULL)) {
        return;
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL) {
        return;
    }

    if (json_object_has_member(root, "llm_provider")) {
        provider = json_object_get_string_member(root, "llm_provider");
    }
    if (json_object_has_member(root, "llm_model")) {
        model = json_object_get_string_member(root, "llm_model");
    }
    if (json_object_has_member(root, "run_id")) {
        run_id = json_object_get_string_member(root, "run_id");
    }
    if (json_object_has_member(root, "ttft_ms")) {
        ttft_ms = json_object_get_int_member(root, "ttft_ms");
    }
    if (json_object_has_member(root, "gen_ms")) {
        gen_ms = json_object_get_int_member(root, "gen_ms");
    }
    if (json_object_has_member(root, "validation_passed")) {
        validation_known = TRUE;
        validation_passed = json_object_get_boolean_member(root, "validation_passed");
    }
    if (json_object_has_member(root, "tools_used")) {
        tools = json_object_get_array_member(root, "tools_used");
    }

    // Extract memory context mode
    if (json_object_has_member(root, "metadata")) {
        JsonObject *metadata = json_object_get_object_member(root, "metadata");
        if (metadata != NULL && json_object_has_member(metadata, "context_debug")) {
            JsonObject *context_debug = json_object_get_object_member(metadata, "context_debug");
            if (context_debug != NULL && json_object_has_member(context_debug, "mode")) {
                context_mode = json_object_get_string_member(context_debug, "mode");
            }
        }
    }
    
    // Display memory context indicator first (prominent position)
    if (context_mode != NULL && context_mode[0] != '\0') {
        if (g_strcmp0(context_mode, "MEMORY") == 0) {
            append_metadata_chip(meta_box, "✨ Memory");
        } else {
            g_autofree char *context_chip = g_strdup_printf("📋 %s", context_mode);
            append_metadata_chip(meta_box, context_chip);
        }
    }

    if ((provider != NULL && provider[0] != '\0') || (model != NULL && model[0] != '\0')) {
        g_autofree char *provider_chip = g_strdup_printf(
            "%s / %s",
            provider != NULL && provider[0] != '\0' ? provider : "unknown",
            model != NULL && model[0] != '\0' ? model : "unknown"
        );
        append_metadata_chip(meta_box, provider_chip);
    }
    if (run_id != NULL && run_id[0] != '\0') {
        g_autofree char *run_chip = g_strdup_printf("run %s", run_id);
        append_metadata_chip(meta_box, run_chip);
    }

    if (ttft_ms >= 0) {
        g_autofree char *ttft_chip = g_strdup_printf("TTFT %lld ms", (long long) ttft_ms);
        append_metadata_chip(meta_box, ttft_chip);
    }
    if (gen_ms >= 0) {
        g_autofree char *gen_chip = g_strdup_printf("GEN %lld ms", (long long) gen_ms);
        append_metadata_chip(meta_box, gen_chip);
    }
    if (validation_known) {
        append_metadata_chip(meta_box, validation_passed ? "Validation passed" : "Validation failed");
    }
    if (tools != NULL) {
        guint i;
        g_autofree char *tools_chip = g_strdup_printf(
            "%u tool%s",
            json_array_get_length(tools),
            json_array_get_length(tools) == 1 ? "" : "s"
        );
        append_metadata_chip(meta_box, tools_chip);
        for (i = 0; i < MIN((guint) 3, json_array_get_length(tools)); i++) {
            const char *tool_name = json_array_get_string_element(tools, i);
            if (tool_name != NULL && tool_name[0] != '\0') {
                append_metadata_chip(meta_box, tool_name);
            }
        }
    }

    if (gtk_widget_get_first_child(meta_box) != NULL) {
        gtk_widget_set_visible(meta_box, TRUE);
    }
}

static void
reset_stream_ui(LiaraWindow *ui, const char *status_message)
{
    ui->stream_inflight = FALSE;
    ui->current_assistant_content = NULL;
    ui->current_assistant_meta_box = NULL;
    ui->current_assistant_label = NULL;
    ui->current_assistant_started = FALSE;
    ui->stream_api_heartbeat_seen = FALSE;

    if (ui->stream_watchdog_source_id != 0) {
        g_source_remove(ui->stream_watchdog_source_id);
        ui->stream_watchdog_source_id = 0;
    }

    gtk_widget_set_sensitive(ui->chat_send_button, TRUE);
    gtk_button_set_label(GTK_BUTTON(ui->chat_send_button), "Send");
    set_stream_status(ui, "STREAM IDLE", "Waiting for the next request.", "stream-status-idle");

    if (status_message != NULL) {
        set_text_view_text(ui->status_output, status_message);
    }
}

static gboolean
on_stream_watchdog_tick(gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const gint64 now = g_get_monotonic_time();
    const gint64 timeout_seconds = (gint64) CLAMP(ui->client_stream_watchdog_seconds, 15, 900);
    const gint64 timeout_usec = timeout_seconds * G_USEC_PER_SEC;
    g_autofree char *status_message = NULL;

    if (!ui->stream_inflight) {
        ui->stream_watchdog_source_id = 0;
        return G_SOURCE_REMOVE;
    }

    if (ui->stream_api_heartbeat_seen) {
        g_message("[WATCHDOG] Heartbeat bypass ACTIVE - continuing stream");
        return G_SOURCE_CONTINUE;
    }

    if ((now - ui->stream_last_event_usec) <= timeout_usec) {
        return G_SOURCE_CONTINUE;
    }

    g_message("[WATCHDOG] TIMEOUT after %.1fs - no heartbeat seen", (double)timeout_seconds);

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
            NULL,
            NULL
        );
    }

    status_message = g_strdup_printf(
        "Watchdog timeout: no stream event or heartbeat received for %lld seconds. The UI was reset.",
        (long long) timeout_seconds
    );

    set_stream_status(ui, "STREAM TIMEOUT", "No heartbeat or chunk arrived before the local watchdog limit.", "stream-status-error");

    reset_stream_ui(ui, status_message);
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
        const char *created_at = json_object_has_member(item, "created_at") ? json_object_get_string_member(item, "created_at") : NULL;
        const char *run_id = json_object_has_member(item, "run_id") ? json_object_get_string_member(item, "run_id") : NULL;
        GtkWidget *content_box = append_history_message(ui, role, content);
        GtkWidget *meta_box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);

        gtk_widget_add_css_class(meta_box, "chat-bubble-meta");
        gtk_box_append(GTK_BOX(content_box), meta_box);
        append_metadata_chip(meta_box, created_at);
        if (run_id != NULL && run_id[0] != '\0') {
            g_autofree char *run_chip = g_strdup_printf("run %s", run_id);
            append_metadata_chip(meta_box, run_chip);
        }
    }
}

static void
on_explorer_response(const char *response_text, GError *error, gpointer user_data)
{
    ExplorerRequestContext *context = user_data;
    LiaraWindow *ui;
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonObject *root;
    const char *status;

    if (context == NULL || context->ui == NULL) {
        explorer_request_context_free(context);
        return;
    }

    ui = context->ui;

    if (error != NULL) {
        g_autofree char *message = g_strdup_printf("Explorer request failed: %s", error->message);
        set_explorer_status(ui, message);
        set_text_view_text(ui->explorer_preview, message);
        g_error_free(error);
        explorer_request_context_free(context);
        return;
    }

    if (response_text == NULL || !json_parser_load_from_data(parser, response_text, -1, NULL)) {
        set_explorer_status(ui, "Explorer response could not be parsed.");
        set_text_view_text(ui->explorer_preview, "Explorer response could not be parsed.");
        explorer_request_context_free(context);
        return;
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL) {
        set_explorer_status(ui, "Explorer response was empty.");
        explorer_request_context_free(context);
        return;
    }

    status = json_object_has_member(root, "status") ? json_object_get_string_member(root, "status") : "failed";
    if (g_strcmp0(status, "success") != 0) {
        const char *api_error = json_object_has_member(root, "error") ? json_object_get_string_member(root, "error") : "Request failed";
        set_explorer_status(ui, api_error);
        set_text_view_text(ui->explorer_preview, api_error);
        explorer_request_context_free(context);
        return;
    }

    if (!json_object_has_member(root, "output")) {
        set_explorer_status(ui, "Explorer response had no output field.");
        explorer_request_context_free(context);
        return;
    }

    if (context->kind == EXPLORER_REQUEST_LIST) {
        const char *output_text = json_object_get_string_member(root, "output");
        g_autofree char *status_text = g_strdup_printf("Directory loaded: %s", context->path);
        set_explorer_status(ui, status_text);
        set_text_view_text(ui->explorer_preview, "Select a file to preview its content.");
        populate_explorer_list_from_ls(ui, context->path, output_text);
    } else {
        const char *output_text = json_object_get_string_member(root, "output");
        g_autofree char *status_text = g_strdup_printf("Preview loaded: %s", context->path);
        set_explorer_status(ui, status_text);
        set_text_view_text(ui->explorer_preview, output_text != NULL ? output_text : "");
    }

    explorer_request_context_free(context);
}

static void
on_session_snapshot_loaded(const char *response_text, GError *error, gpointer user_data)
{
    LiaraWindow *ui = user_data;

    if (error != NULL) {
        if (ui->dev_mode) {
            g_autofree char *message = g_strdup_printf("Session request failed\n\n%s", error->message);
            set_text_view_text(ui->status_output, message);
        }
        render_session_snapshot(ui, NULL);
        g_error_free(error);
        return;
    }

    if (ui->dev_mode) {
        set_text_view_text(ui->status_output, response_text);
    }
    render_session_snapshot(ui, response_text);
}

static void
on_status_health_loaded(const char *response_text, GError *error, gpointer user_data)
{
    LiaraWindow *ui = user_data;

    if (error != NULL) {
        g_autofree char *message = g_strdup_printf("Health request failed\n\n%s", error->message);
        set_text_view_text(ui->status_output, message);
        render_health_cards(ui->status_health_cards, ui->status_health_summary, NULL, FALSE);
        g_error_free(error);
        return;
    }

    set_text_view_text(ui->status_output, response_text);
    render_health_cards(ui->status_health_cards, ui->status_health_summary, response_text, FALSE);
}

static void
on_status_backends_loaded(const char *response_text, GError *error, gpointer user_data)
{
    LiaraWindow *ui = user_data;

    if (error != NULL) {
        g_autofree char *message = g_strdup_printf("Backend health request failed\n\n%s", error->message);
        set_text_view_text(ui->status_output, message);
        render_health_cards(ui->status_health_cards, ui->status_health_summary, NULL, TRUE);
        g_error_free(error);
        return;
    }

    set_text_view_text(ui->status_output, response_text);
    render_health_cards(ui->status_health_cards, ui->status_health_summary, response_text, TRUE);
}

static void
on_startup_greeting_loaded(const char *response_text, GError *error, gpointer user_data)
{
    LiaraWindow *ui = user_data;

    if (error != NULL) {
        append_chat_message(
            ui,
            "LIARA",
            "Willkommen. Die persoenliche Begruessung konnte gerade nicht geladen werden, aber ich bin bereit.",
            TRUE,
            NULL,
            NULL
        );
        g_error_free(error);
        return;
    }

    if (response_text != NULL) {
        g_autofree char *greeting = extract_response_from_chat_payload(response_text);
        if (greeting != NULL && greeting[0] != '\0') {
            append_chat_message(ui, "LIARA", greeting, TRUE, NULL, NULL);
            return;
        }
    }

    append_chat_message(ui, "LIARA", "Willkommen. Ich bin bereit.", TRUE, NULL, NULL);
}

static void
request_startup_greeting(LiaraWindow *ui)
{
    const char *display_name = ui->identity_display_name != NULL
        ? gtk_label_get_text(ui->identity_display_name)
        : "User";
    const char *user_id = gtk_editable_get_text(GTK_EDITABLE(ui->user_entry));
    const char *session_id = gtk_editable_get_text(GTK_EDITABLE(ui->session_entry));
    const char *sandbox_root = gtk_editable_get_text(GTK_EDITABLE(ui->session_sandbox_entry));
    g_autofree char *welcome_session_id = NULL;
    g_autofree char *prompt = NULL;

    if (session_id == NULL || session_id[0] == '\0' || user_id == NULL || user_id[0] == '\0') {
        return;
    }

    welcome_session_id = g_strdup_printf("%s-welcome", session_id);
    prompt = g_strdup_printf(
        "Schreibe eine kurze persoenliche Begruessung auf Deutsch fuer %s. "
        "Maximal zwei Saetze, warm, klar, ohne Markdown, ohne Aufzaehlungen.",
        display_name != NULL && display_name[0] != '\0' ? display_name : "den Nutzer"
    );

    liara_api_post_chat(
        ui->api,
        welcome_session_id,
        user_id,
        display_name,
        prompt,
        sandbox_root,
        96,
        on_startup_greeting_loaded,
        ui
    );
}

static void
populate_tool_parameter_box(GtkWidget *box, JsonArray *params, const char *empty_text)
{
    guint i;

    clear_box_children(box);

    if (params == NULL || json_array_get_length(params) == 0) {
        gtk_box_append(GTK_BOX(box), create_metadata_chip(empty_text));
        return;
    }

    for (i = 0; i < json_array_get_length(params); i++) {
        const char *param = json_array_get_string_element(params, i);
        if (param != NULL && param[0] != '\0') {
            gtk_box_append(GTK_BOX(box), create_metadata_chip(param));
        }
    }
}

static void
on_tool_row_activated(GtkListBox *box, GtkListBoxRow *row, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *tool_name;

    (void) box;
    if (row == NULL) {
        return;
    }

    tool_name = g_object_get_data(G_OBJECT(row), "tool-name");
    if (tool_name == NULL || tool_name[0] == '\0') {
        return;
    }

    gtk_editable_set_text(GTK_EDITABLE(ui->tool_name_entry), tool_name);
    liara_api_get_tool_metadata(ui->api, tool_name, on_tool_metadata_loaded, ui);
}

static GtkWidget *
create_tool_list_row(const char *tool_name, const char *description)
{
    GtkWidget *row = gtk_list_box_row_new();
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    GtkWidget *name_label = gtk_label_new(tool_name);
    GtkWidget *desc_label = gtk_label_new(description != NULL ? description : "");

    gtk_widget_add_css_class(box, "tool-list-row");
    gtk_widget_add_css_class(name_label, "nav-title");
    gtk_widget_add_css_class(desc_label, "nav-subtitle");
    gtk_label_set_xalign(GTK_LABEL(name_label), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(desc_label), 0.0f);
    gtk_label_set_wrap(GTK_LABEL(desc_label), TRUE);

    gtk_box_append(GTK_BOX(box), name_label);
    gtk_box_append(GTK_BOX(box), desc_label);
    gtk_list_box_row_set_child(GTK_LIST_BOX_ROW(row), box);
    g_object_set_data_full(G_OBJECT(row), "tool-name", g_strdup(tool_name), g_free);
    return row;
}

static void
on_tools_loaded(const char *response_text, GError *error, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonObject *root;
    JsonArray *tools;
    guint i;

    clear_box_children(ui->tool_list_box);

    if (error != NULL) {
        g_autofree char *message = g_strdup_printf("Tool list request failed\n\n%s", error->message);
        set_text_view_text(ui->tools_output, message);
        gtk_list_box_append(GTK_LIST_BOX(ui->tool_list_box), create_tool_list_row("Unavailable", "Tool list could not be loaded."));
        g_error_free(error);
        return;
    }

    set_text_view_text(ui->tools_output, response_text);

    if (response_text == NULL || !json_parser_load_from_data(parser, response_text, -1, NULL)) {
        gtk_list_box_append(GTK_LIST_BOX(ui->tool_list_box), create_tool_list_row("Parse error", "Could not parse tool list response."));
        return;
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL || !json_object_has_member(root, "tools")) {
        gtk_list_box_append(GTK_LIST_BOX(ui->tool_list_box), create_tool_list_row("No payload", "Response did not include tools."));
        return;
    }

    tools = json_object_get_array_member(root, "tools");
    if (tools == NULL || json_array_get_length(tools) == 0) {
        gtk_list_box_append(GTK_LIST_BOX(ui->tool_list_box), create_tool_list_row("No tools", "No tools are currently available."));
        return;
    }

    for (i = 0; i < json_array_get_length(tools); i++) {
        JsonObject *tool = json_array_get_object_element(tools, i);
        const char *name = json_object_has_member(tool, "name") ? json_object_get_string_member(tool, "name") : "unknown";
        const char *description = json_object_has_member(tool, "description") ? json_object_get_string_member(tool, "description") : "";

        gtk_list_box_append(GTK_LIST_BOX(ui->tool_list_box), create_tool_list_row(name, description));
    }
}

static void
on_tool_metadata_loaded(const char *response_text, GError *error, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonObject *root;
    JsonObject *tool;
    const char *name = "Unknown tool";
    const char *description = "No description available.";
    JsonArray *required = NULL;
    JsonArray *optional = NULL;

    if (error != NULL) {
        g_autofree char *message = g_strdup_printf("Tool metadata request failed\n\n%s", error->message);
        set_text_view_text(ui->tools_output, message);
        gtk_label_set_text(ui->tool_detail_name, "Tool lookup failed");
        gtk_label_set_text(ui->tool_detail_description, error->message);
        populate_tool_parameter_box(ui->tool_required_params_box, NULL, "No required parameters");
        populate_tool_parameter_box(ui->tool_optional_params_box, NULL, "No optional parameters");
        g_error_free(error);
        return;
    }

    set_text_view_text(ui->tools_output, response_text);

    if (response_text == NULL || !json_parser_load_from_data(parser, response_text, -1, NULL)) {
        gtk_label_set_text(ui->tool_detail_name, "Tool metadata unavailable");
        gtk_label_set_text(ui->tool_detail_description, "Response could not be parsed.");
        populate_tool_parameter_box(ui->tool_required_params_box, NULL, "No required parameters");
        populate_tool_parameter_box(ui->tool_optional_params_box, NULL, "No optional parameters");
        return;
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL || !json_object_has_member(root, "tool")) {
        gtk_label_set_text(ui->tool_detail_name, "Tool metadata unavailable");
        gtk_label_set_text(ui->tool_detail_description, "Response did not contain a tool payload.");
        populate_tool_parameter_box(ui->tool_required_params_box, NULL, "No required parameters");
        populate_tool_parameter_box(ui->tool_optional_params_box, NULL, "No optional parameters");
        return;
    }

    tool = json_object_get_object_member(root, "tool");
    if (json_object_has_member(tool, "name")) {
        name = json_object_get_string_member(tool, "name");
    }
    if (json_object_has_member(tool, "description")) {
        description = json_object_get_string_member(tool, "description");
    }
    if (json_object_has_member(tool, "required_parameters")) {
        required = json_object_get_array_member(tool, "required_parameters");
    }
    if (json_object_has_member(tool, "optional_parameters")) {
        optional = json_object_get_array_member(tool, "optional_parameters");
    }

    gtk_label_set_text(ui->tool_detail_name, name);
    gtk_label_set_text(ui->tool_detail_description, description);
    populate_tool_parameter_box(ui->tool_required_params_box, required, "No required parameters");
    populate_tool_parameter_box(ui->tool_optional_params_box, optional, "No optional parameters");
    populate_dynamic_tool_form(ui, required, optional);
}

static void
on_chat_stream_chunk(const char *chunk_text, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *existing_text;
    g_autofree char *updated_text = NULL;

    if (!ui->stream_inflight) {
        g_message("[CHUNK] Stream not inflight, ignoring chunk");
        return;
    }

    if (ui->current_assistant_label == NULL) {
        g_message("[CHUNK] ERROR: current_assistant_label is NULL! Bubble not created?");
        return;
    }

    if (chunk_text == NULL || chunk_text[0] == '\0') {
        g_message("[CHUNK] Empty chunk, skipping");
        return;
    }

    g_message("[CHUNK] Received chunk: %.50s...", chunk_text);
    mark_stream_activity(ui);
    set_stream_status(ui, "STREAM CHUNKING", "Assistant response chunks are arriving.", "stream-status-active");

    if (!ui->current_assistant_started) {
        g_message("[CHUNK] First chunk - setting as initial text");
        gtk_label_set_text(ui->current_assistant_label, chunk_text);
        ui->current_assistant_started = TRUE;
        scroll_chat_to_bottom(ui);
        return;
    }

    g_message("[CHUNK] Appending to existing text");
    existing_text = gtk_label_get_text(ui->current_assistant_label);
    updated_text = g_strconcat(existing_text, chunk_text, NULL);
    gtk_label_set_text(ui->current_assistant_label, updated_text);
    scroll_chat_to_bottom(ui);
}

static void
on_chat_stream_progress(const char *progress_payload, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonObject *root;

    if (!ui->stream_inflight || progress_payload == NULL || progress_payload[0] == '\0') {
        return;
    }

    mark_stream_activity(ui);

    if (!json_parser_load_from_data(parser, progress_payload, -1, NULL)) {
        if (ui->dev_mode) {
            set_text_view_text(ui->status_output, progress_payload);
        }
        return;
    }

    root = json_node_get_object(json_parser_get_root(parser));
    if (root == NULL) {
        if (ui->dev_mode) {
            set_text_view_text(ui->status_output, progress_payload);
        }
        return;
    }

    {
        const char *stage = json_object_has_member(root, "stage") ? json_object_get_string_member(root, "stage") : "running";
        const char *message = json_object_has_member(root, "message") ? json_object_get_string_member(root, "message") : "Receiving stream progress";
        
        // Detect memory effects
        if (g_strcmp0(stage, "memory_effect_detected") == 0) {
            JsonObject *metadata = json_object_has_member(root, "metadata") ? json_object_get_object_member(root, "metadata") : NULL;
            const char *context_mode = (metadata != NULL && json_object_has_member(metadata, "context_mode")) 
                ? json_object_get_string_member(metadata, "context_mode") 
                : "MEMORY";
            g_autofree char *memory_status = g_strdup_printf("✨ Memory Effect Detected\n\nContext Mode: %s\nMessage: %s", context_mode, message);
            set_text_view_text(ui->status_output, memory_status);
            set_stream_status(ui, "STREAM MEMORY_EFFECT_DETECTED", g_strdup_printf("✨ %s", context_mode), "stream-status-active");
        } else {
            g_autofree char *status_message = g_strdup_printf("Stream progress\n\nStage: %s\nMessage: %s", stage, message);
            g_autofree char *badge = g_strdup_printf("STREAM %s", g_ascii_strup(stage, -1));
            set_text_view_text(ui->status_output, status_message);
            set_stream_status(ui, badge, message, "stream-status-active");
        }
    }
}

static void
on_chat_stream_heartbeat(const char *heartbeat_payload, gpointer user_data)
{
    LiaraWindow *ui = user_data;

    if (!ui->stream_inflight) {
        g_message("[HEARTBEAT] Dropped because stream_inflight=FALSE");
        return;
    }

    (void) heartbeat_payload;
    ui->stream_api_heartbeat_seen = TRUE;
    mark_stream_activity(ui);
    g_message("[HEARTBEAT] Received! Flag set to TRUE. Watchdog bypass is NOW ACTIVE.");
    set_stream_status(ui, "STREAM HEARTBEAT", "API heartbeat received. Local watchdog is bypassed.", "stream-status-heartbeat");
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
            append_chat_message(ui, "LIARA", message, TRUE, NULL, NULL);
        }
        set_text_view_text(ui->status_output, message);
        set_stream_status(ui, "STREAM ERROR", error->message, "stream-status-error");
    } else {
        g_autofree char *response_text = extract_response_from_chat_payload(final_payload);

        if (final_payload != NULL) {
            set_text_view_text(ui->status_output, final_payload);
        }

        if (ui->current_assistant_content != NULL && response_text[0] != '\0') {
            render_message_content(ui->current_assistant_content, response_text, TRUE);
            render_artifacts_from_chat_payload(ui->current_assistant_content, final_payload, ui->api);
        } else if (ui->current_assistant_label != NULL && !ui->current_assistant_started && response_text[0] != '\0') {
            gtk_label_set_text(ui->current_assistant_label, response_text);
        }

        render_assistant_metadata(ui->current_assistant_meta_box, final_payload);
        set_stream_status(ui, "STREAM COMPLETE", "Final payload received and rendered.", "stream-status-complete");
    }

    reset_stream_ui(ui, NULL);
    scroll_chat_to_bottom(ui);
}

static void
on_chat_request_loaded(const char *response_text, GError *error, gpointer user_data)
{
    LiaraWindow *ui = user_data;

    if (error != NULL) {
        g_autofree char *message = g_strdup_printf("Chat request failed\n\n%s", error->message);
        if (ui->current_assistant_label != NULL) {
            gtk_label_set_text(ui->current_assistant_label, message);
        } else {
            append_chat_message(ui, "LIARA", message, TRUE, NULL, NULL);
        }
        set_text_view_text(ui->status_output, message);
        g_error_free(error);
        reset_stream_ui(ui, NULL);
        scroll_chat_to_bottom(ui);
        return;
    }

    if (response_text != NULL) {
        g_autofree char *response_value = extract_response_from_chat_payload(response_text);
        if (response_value != NULL && response_value[0] != '\0') {
            if (ui->current_assistant_content != NULL) {
                render_message_content(ui->current_assistant_content, response_value, TRUE);
                render_artifacts_from_chat_payload(ui->current_assistant_content, response_text, ui->api);
            } else if (ui->current_assistant_label != NULL) {
                gtk_label_set_text(ui->current_assistant_label, response_value);
            } else {
                append_chat_message(ui, "LIARA", response_value, TRUE, NULL, NULL);
            }
        }
        set_text_view_text(ui->status_output, response_text);
        render_assistant_metadata(ui->current_assistant_meta_box, response_text);
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
on_nav_explorer_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *path;
    (void) button;
    switch_view(ui, "explorer", "explorer-side");
    path = gtk_editable_get_text(GTK_EDITABLE(ui->explorer_path_entry));
    request_explorer_list(ui, path);
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

/* Chat input handler callback - called when Enter is pressed */
static gboolean
on_chat_input_ready(const char *message, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    
    if (ui == NULL || message == NULL || message[0] == '\0') {
        return FALSE;
    }
    
    /* Simulate button click to reuse existing send logic */
    on_send_chat_clicked(GTK_BUTTON(ui->chat_send_button), ui);
    
    return TRUE;
}

static void
on_nav_settings_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    switch_view(ui, "settings", "settings-side");
}

static void
on_nav_audit_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    switch_view(ui, "audit", "audit-side");
}

static void
on_toggle_inspector_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    gboolean reveal_child;

    (void) button;
    reveal_child = gtk_revealer_get_reveal_child(ui->inspector_revealer);
    gtk_revealer_set_reveal_child(ui->inspector_revealer, !reveal_child);
    gtk_button_set_label(GTK_BUTTON(ui->inspector_toggle_button), reveal_child ? "<" : ">");
}

static void
on_explorer_refresh_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *path = gtk_editable_get_text(GTK_EDITABLE(ui->explorer_path_entry));
    (void) button;
    request_explorer_list(ui, path);
}

static void
on_explorer_home_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    request_explorer_list(ui, "/home/liara");
}

static void
on_explorer_workspace_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    request_explorer_list(ui, "/home/liara/workspace");
}

static void
on_explorer_up_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *path = gtk_editable_get_text(GTK_EDITABLE(ui->explorer_path_entry));
    g_autofree char *parent = g_path_get_dirname(path != NULL ? path : "/home/liara/workspace");
    (void) button;
    request_explorer_list(ui, parent);
}

static void
on_explorer_open_path_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *path = gtk_editable_get_text(GTK_EDITABLE(ui->explorer_path_entry));
    (void) button;
    request_explorer_list(ui, path);
}

static void
on_explorer_row_activated(GtkListBox *box, GtkListBoxRow *row, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    ExplorerItemData *item;

    (void) box;
    if (row == NULL) {
        return;
    }

    item = g_object_get_data(G_OBJECT(row), "explorer-item");
    if (item == NULL || item->path == NULL || item->path[0] == '\0') {
        return;
    }

    if (item->is_dir) {
        request_explorer_list(ui, item->path);
    } else {
        request_explorer_file_preview(ui, item->path);
    }
}

static void
on_send_chat_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    g_autofree char *message = get_text_view_text(ui->chat_input);
    char *trimmed_message = message;
    const char *session_id = gtk_editable_get_text(GTK_EDITABLE(ui->session_entry));
    const char *user_id = gtk_editable_get_text(GTK_EDITABLE(ui->user_entry));
    const char *display_name = ui->identity_display_name != NULL
        ? gtk_label_get_text(ui->identity_display_name)
        : NULL;
    const char *sandbox_root = gtk_editable_get_text(GTK_EDITABLE(ui->session_sandbox_entry));
    int max_tokens = gtk_spin_button_get_value_as_int(ui->max_tokens);

    (void) button;

    trimmed_message = g_strstrip(trimmed_message);
    if (ui->stream_inflight || trimmed_message[0] == '\0') {
        return;
    }

    gtk_editable_set_text(GTK_EDITABLE(ui->session_info_session_entry), session_id);
    gtk_editable_set_text(GTK_EDITABLE(ui->session_info_user_entry), user_id);
    append_chat_message(ui, "You", trimmed_message, FALSE, NULL, NULL);
        g_message("[SEND] Creating assistant bubble for streaming response");
    ui->current_assistant_content = append_chat_message(
        ui,
        "LIARA",
        "Thinking ...",
        TRUE,
        &ui->current_assistant_label,
        &ui->current_assistant_meta_box
    );
    ui->current_assistant_started = FALSE;
    ui->stream_inflight = TRUE;
    ui->stream_api_heartbeat_seen = FALSE;
    set_stream_status(ui, "STREAM STARTED", "Request accepted by the client and sent to the API.", "stream-status-active");
    mark_stream_activity(ui);
    if (ui->stream_watchdog_source_id != 0) {
        g_source_remove(ui->stream_watchdog_source_id);
    }
    ui->stream_watchdog_source_id = g_timeout_add_seconds(1, on_stream_watchdog_tick, ui);
    gtk_widget_set_sensitive(ui->chat_send_button, FALSE);
    gtk_button_set_label(GTK_BUTTON(ui->chat_send_button), "Streaming...");
    set_text_view_text(ui->chat_input, "");
    switch_view(ui, "chat", "status");

    if (ui->client_stream_enabled) {
        liara_api_post_chat_stream(
            ui->api,
            session_id,
            user_id,
            display_name,
            trimmed_message,
            sandbox_root,
            max_tokens,
            on_chat_stream_chunk,
            on_chat_stream_progress,
            on_chat_stream_heartbeat,
            on_chat_stream_complete,
            ui
        );
    } else {
        if (ui->stream_watchdog_source_id != 0) {
            g_source_remove(ui->stream_watchdog_source_id);
            ui->stream_watchdog_source_id = 0;
        }
        gtk_button_set_label(GTK_BUTTON(ui->chat_send_button), "Waiting...");
        liara_api_post_chat(
            ui->api,
            session_id,
            user_id,
            display_name,
            trimmed_message,
            sandbox_root,
            max_tokens,
            on_chat_request_loaded,
            ui
        );
    }
}

static void
on_refresh_history_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *session_id = gtk_editable_get_text(GTK_EDITABLE(ui->history_session_entry));
    const char *run_id = ui->history_run_id_entry != NULL
        ? gtk_editable_get_text(GTK_EDITABLE(ui->history_run_id_entry))
        : NULL;
    int limit = gtk_spin_button_get_value_as_int(ui->history_limit);
    gboolean include_tools = gtk_check_button_get_active(ui->history_include_tools);

    (void) button;

    liara_api_get_history(
        ui->api,
        session_id,
        run_id,
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
    gboolean migrated_from_bridge = is_legacy_bridge_base_url(raw_url);

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
        g_autofree char *message = NULL;
        if (migrated_from_bridge) {
            message = g_strdup_printf(
                "Connection settings applied with automatic migration.\n\nInput endpoint: %s\nDirect API endpoint: %s\nConfig: %s",
                raw_url != NULL ? raw_url : "(empty)",
                base_url,
                ui->config_path
            );
            if (ui->settings_saved_hint != NULL) {
                gtk_label_set_text(ui->settings_saved_hint, "Endpoint migrated from bridge to direct API (8010).");
            }
        } else {
            message = g_strdup_printf(
                "Connection settings applied.\n\nBase URL: %s\nConfig: %s",
                base_url,
                ui->config_path
            );
        }
        set_text_view_text(ui->status_output, message);
    }
}

static void
on_apply_client_settings_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    g_autoptr(GError) error = NULL;
    const char *base_url = liara_api_get_base_url(ui->api);

    (void) button;

    sync_client_settings_from_widgets(ui);
    sync_client_settings_to_widgets(ui);

    if (!save_connection_config(ui, base_url, &error)) {
        g_autofree char *message = g_strdup_printf(
            "Client settings updated, but save failed\n\n%s",
            error != NULL ? error->message : "unknown error"
        );
        if (ui->settings_saved_hint != NULL) {
            gtk_label_set_text(ui->settings_saved_hint, message);
        }
        set_text_view_text(ui->status_output, message);
        return;
    }

    if (ui->settings_saved_hint != NULL) {
        gtk_label_set_text(ui->settings_saved_hint, "Client settings saved to lserv.json.");
    }
    set_text_view_text(ui->status_output, "Client settings saved.");
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
    liara_api_get_session(ui->api, session_id, user_id, on_session_snapshot_loaded, ui);
}

static void
on_save_session_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *session_id = gtk_editable_get_text(GTK_EDITABLE(ui->session_info_session_entry));
    const char *user_id = gtk_editable_get_text(GTK_EDITABLE(ui->session_info_user_entry));
    const char *sandbox_root = gtk_editable_get_text(GTK_EDITABLE(ui->session_sandbox_entry));
    g_autofree char *metadata_json = NULL;

    (void) button;
    gtk_editable_set_text(GTK_EDITABLE(ui->session_entry), session_id);
    gtk_editable_set_text(GTK_EDITABLE(ui->user_entry), user_id);
    metadata_json = build_session_metadata_json(ui);
    liara_api_post_session(
        ui->api,
        session_id,
        user_id,
        sandbox_root,
        metadata_json,
        on_session_snapshot_loaded,
        ui
    );
}

static void
on_health_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    liara_api_get_health(ui->api, on_status_health_loaded, ui);
}

static void
on_health_backends_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    liara_api_get_health_backends(ui->api, on_status_backends_loaded, ui);
}

static void
on_list_tools_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    (void) button;
    liara_api_get_tools(ui->api, on_tools_loaded, ui);
}

static void
on_tool_details_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *tool_name = gtk_editable_get_text(GTK_EDITABLE(ui->tool_name_entry));

    (void) button;
    liara_api_get_tool_metadata(ui->api, tool_name, on_tool_metadata_loaded, ui);
}

static void
on_invoke_tool_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    g_autofree char *parameters = build_tool_parameters_json(ui);
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

static void
on_audit_summary_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    int limit = gtk_spin_button_get_value_as_int(ui->audit_limit);
    gboolean blocked_only = gtk_check_button_get_active(ui->audit_blocked_only);
    const char *source = gtk_editable_get_text(GTK_EDITABLE(ui->audit_source_entry));
    const char *risk_level = gtk_editable_get_text(GTK_EDITABLE(ui->audit_risk_entry));
    const char *command_family = gtk_editable_get_text(GTK_EDITABLE(ui->audit_family_entry));

    (void) button;

    liara_api_get_sys_audit_summary(
        ui->api,
        limit,
        blocked_only,
        source,
        risk_level,
        command_family,
        on_audit_response_loaded,
        ui->audit_output
    );
}

static void
on_audit_suspicious_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    int limit = gtk_spin_button_get_value_as_int(ui->audit_limit);
    int max_items = gtk_spin_button_get_value_as_int(ui->audit_max_items);
    gboolean blocked_only = gtk_check_button_get_active(ui->audit_blocked_only);
    const char *source = gtk_editable_get_text(GTK_EDITABLE(ui->audit_source_entry));
    const char *risk_level = gtk_editable_get_text(GTK_EDITABLE(ui->audit_risk_entry));
    const char *command_family = gtk_editable_get_text(GTK_EDITABLE(ui->audit_family_entry));

    (void) button;

    liara_api_get_sys_audit_suspicious(
        ui->api,
        limit,
        max_items,
        blocked_only,
        source,
        risk_level,
        command_family,
        on_audit_response_loaded,
        ui->audit_output
    );
}

static void
on_audit_preset_clicked(GtkButton *button, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *preset = gtk_editable_get_text(GTK_EDITABLE(ui->audit_preset_entry));
    int limit = gtk_spin_button_get_value_as_int(ui->audit_limit);
    int max_items = gtk_spin_button_get_value_as_int(ui->audit_max_items);

    (void) button;

    liara_api_get_sys_audit_preset(
        ui->api,
        preset,
        limit,
        max_items,
        on_audit_response_loaded,
        ui->audit_output
    );
}

static GtkWidget *
make_panel_title(const char *eyebrow, const char *title, const char *subtitle)
{
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    GtkWidget *eyebrow_label = gtk_label_new(eyebrow);
    GtkWidget *title_label = gtk_label_new(title);
    GtkWidget *subtitle_label = subtitle != NULL ? gtk_label_new(subtitle) : NULL;

    gtk_widget_add_css_class(eyebrow_label, "eyebrow");
    gtk_widget_add_css_class(title_label, "panel-title");
    if (subtitle_label != NULL) {
        gtk_widget_add_css_class(subtitle_label, "panel-subtitle");
    }
    gtk_label_set_xalign(GTK_LABEL(eyebrow_label), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(title_label), 0.0f);
    if (subtitle_label != NULL) {
        gtk_label_set_xalign(GTK_LABEL(subtitle_label), 0.0f);
        gtk_label_set_wrap(GTK_LABEL(subtitle_label), TRUE);
        gtk_label_set_wrap_mode(GTK_LABEL(subtitle_label), PANGO_WRAP_WORD_CHAR);
        gtk_label_set_max_width_chars(GTK_LABEL(subtitle_label), 56);
    }

    gtk_box_append(GTK_BOX(box), eyebrow_label);
    gtk_box_append(GTK_BOX(box), title_label);
    if (subtitle_label != NULL) {
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
    gtk_widget_set_hexpand(scrolled, TRUE);
    gtk_scrolled_window_set_propagate_natural_width(GTK_SCROLLED_WINDOW(scrolled), FALSE);
    gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(scrolled), FALSE);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scrolled), view);
    *out_view = GTK_TEXT_VIEW(view);
    return scrolled;
}

static GtkWidget *
wrap_side_panel(GtkWidget *child)
{
    GtkWidget *scrolled = gtk_scrolled_window_new();

    gtk_widget_set_hexpand(scrolled, TRUE);
    gtk_widget_set_vexpand(scrolled, TRUE);
    gtk_scrolled_window_set_policy(GTK_SCROLLED_WINDOW(scrolled), GTK_POLICY_NEVER, GTK_POLICY_AUTOMATIC);
    gtk_scrolled_window_set_propagate_natural_width(GTK_SCROLLED_WINDOW(scrolled), FALSE);
    gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(scrolled), FALSE);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scrolled), child);
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
    GtkWidget *identity_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    GtkWidget *identity_name = gtk_label_new("Windows User");
    GtkWidget *identity_login = gtk_label_new("user  |  session");
    GtkWidget *dev_badge = gtk_label_new("DEV");

    gtk_widget_add_css_class(sidebar, "sidebar");
    gtk_widget_add_css_class(brand_card, "brand-card");
    gtk_widget_add_css_class(identity_box, "identity-box");
    gtk_widget_add_css_class(identity_name, "identity-name");
    gtk_widget_add_css_class(identity_login, "identity-login");
    gtk_widget_add_css_class(dev_badge, "dev-badge");
    gtk_label_set_xalign(GTK_LABEL(identity_name), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(identity_login), 0.0f);
    gtk_label_set_wrap(GTK_LABEL(identity_login), TRUE);
    gtk_label_set_xalign(GTK_LABEL(dev_badge), 0.0f);

    ui->identity_display_name = GTK_LABEL(identity_name);
    ui->identity_login_name = GTK_LABEL(identity_login);

    gtk_box_append(GTK_BOX(brand_card), make_panel_title("assistant", "LIARA", "Local intelligence with service boundaries"));
    gtk_box_append(GTK_BOX(identity_box), identity_name);
    gtk_box_append(GTK_BOX(identity_box), identity_login);
    if (ui->dev_mode) {
        gtk_box_append(GTK_BOX(identity_box), dev_badge);
    }
    gtk_box_append(GTK_BOX(brand_card), identity_box);
    gtk_box_append(GTK_BOX(sidebar), brand_card);
    gtk_box_append(GTK_BOX(sidebar), make_sidebar_button("Chat", "Conversation and response flow", G_CALLBACK(on_nav_chat_clicked), ui));
    gtk_box_append(GTK_BOX(sidebar), make_sidebar_button("History", "Session transcript and memory recall", G_CALLBACK(on_nav_history_clicked), ui));
    gtk_box_append(GTK_BOX(sidebar), make_sidebar_button("Explorer", "Browse Liara home and workspace files", G_CALLBACK(on_nav_explorer_clicked), ui));
    if (ui->dev_mode) {
        gtk_box_append(GTK_BOX(sidebar), make_sidebar_button("Tools", "Discover and invoke builtins", G_CALLBACK(on_nav_tools_clicked), ui));
        gtk_box_append(GTK_BOX(sidebar), make_sidebar_button("Audit", "Sys audit history and suspicious traces", G_CALLBACK(on_nav_audit_clicked), ui));
    }
    gtk_box_append(GTK_BOX(sidebar), make_sidebar_button("Status", "API and memory health", G_CALLBACK(on_nav_status_clicked), ui));
    gtk_box_append(GTK_BOX(sidebar), make_sidebar_button("Settings", "Client-side preferences", G_CALLBACK(on_nav_settings_clicked), ui));

    return sidebar;
}

static GtkWidget *
build_chat_view(LiaraWindow *ui)
{
    GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    GtkWidget *hero = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget *chips = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *stream_status_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *transcript_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    GtkWidget *transcript_scroller = gtk_scrolled_window_new();
    GtkWidget *messages_box = gtk_list_box_new();
    GtkWidget *composer_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    GtkWidget *controls = gtk_grid_new();
    GtkWidget *stream_status_badge = gtk_label_new("STREAM IDLE");
    GtkWidget *stream_status_detail = gtk_label_new("Waiting for the next request.");
    GtkWidget *send_button = gtk_button_new_with_label("Send");
    GtkWidget *input_view = make_editor_view(&ui->chat_input, "composer-input", TRUE);

    ui->session_entry = GTK_ENTRY(gtk_entry_new());
    ui->user_entry = GTK_ENTRY(gtk_entry_new());
    ui->max_tokens = GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1, 8192, 1));
    ui->chat_transcript_scroller = GTK_SCROLLED_WINDOW(transcript_scroller);
    ui->chat_messages_box = messages_box;
    ui->chat_send_button = send_button;
    ui->stream_status_badge = GTK_LABEL(stream_status_badge);
    ui->stream_status_detail = GTK_LABEL(stream_status_detail);

    gtk_spin_button_set_value(ui->max_tokens, ui->client_default_max_tokens);

    gtk_widget_add_css_class(page, "content-page");
    gtk_widget_add_css_class(hero, "hero");
    gtk_widget_add_css_class(transcript_card, "surface-card");
    gtk_widget_add_css_class(transcript_scroller, "chat-transcript");
    gtk_widget_add_css_class(messages_box, "chat-messages");
    gtk_widget_add_css_class(composer_card, "surface-card");
    gtk_widget_add_css_class(composer_card, "composer-card");
    gtk_widget_add_css_class(send_button, "suggested-action");
    gtk_widget_add_css_class(stream_status_row, "stream-status-row");
    gtk_widget_add_css_class(stream_status_badge, "stream-status-badge");
    gtk_widget_add_css_class(stream_status_badge, "stream-status-idle");
    gtk_widget_add_css_class(stream_status_detail, "stream-status-detail");
    gtk_grid_set_row_spacing(GTK_GRID(controls), 6);
    gtk_grid_set_column_spacing(GTK_GRID(controls), 8);
    gtk_widget_set_vexpand(transcript_scroller, TRUE);
    gtk_widget_set_hexpand(transcript_scroller, TRUE);
    gtk_widget_set_vexpand(transcript_card, TRUE);
    gtk_widget_set_hexpand(transcript_card, TRUE);
    gtk_widget_set_valign(messages_box, GTK_ALIGN_START);
    gtk_list_box_set_selection_mode(GTK_LIST_BOX(messages_box), GTK_SELECTION_NONE);
    gtk_widget_set_vexpand(input_view, FALSE);
    gtk_widget_set_size_request(input_view, -1, 150);
    gtk_scrolled_window_set_propagate_natural_width(GTK_SCROLLED_WINDOW(transcript_scroller), FALSE);
    gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(transcript_scroller), FALSE);

    gtk_box_append(GTK_BOX(hero), make_panel_title("workspace", "Chat", "ChatGPT-like flow with a native GTK shell"));
    gtk_box_append(GTK_BOX(chips), gtk_label_new("local api"));
    gtk_box_append(GTK_BOX(chips), gtk_label_new("github-style text"));
    gtk_box_append(GTK_BOX(chips), gtk_label_new("cortana accent"));
    gtk_widget_add_css_class(chips, "chip-row");
    gtk_box_append(GTK_BOX(hero), chips);
    if (ui->dev_mode) {
        gtk_label_set_xalign(GTK_LABEL(stream_status_badge), 0.0f);
        gtk_label_set_xalign(GTK_LABEL(stream_status_detail), 0.0f);
        gtk_label_set_wrap(GTK_LABEL(stream_status_detail), TRUE);
        gtk_box_append(GTK_BOX(stream_status_row), stream_status_badge);
        gtk_box_append(GTK_BOX(stream_status_row), stream_status_detail);
        gtk_box_append(GTK_BOX(hero), stream_status_row);
    }

    gtk_box_append(GTK_BOX(composer_card), make_panel_title("compose", "Prompt", NULL));
    if (ui->dev_mode) {
        gtk_grid_attach(GTK_GRID(controls), gtk_label_new("Session"), 0, 0, 1, 1);
        gtk_grid_attach(GTK_GRID(controls), GTK_WIDGET(ui->session_entry), 1, 0, 1, 1);
        gtk_grid_attach(GTK_GRID(controls), gtk_label_new("User"), 2, 0, 1, 1);
        gtk_grid_attach(GTK_GRID(controls), GTK_WIDGET(ui->user_entry), 3, 0, 1, 1);
        gtk_grid_attach(GTK_GRID(controls), gtk_label_new("Max tokens"), 4, 0, 1, 1);
        gtk_grid_attach(GTK_GRID(controls), GTK_WIDGET(ui->max_tokens), 5, 0, 1, 1);
        gtk_box_append(GTK_BOX(composer_card), controls);
    }
    gtk_box_append(GTK_BOX(composer_card), input_view);
    gtk_box_append(GTK_BOX(composer_card), send_button);

    gtk_box_append(GTK_BOX(transcript_card), make_panel_title("assistant", "Transcript", "Streaming chat bubbles over the live /chat/stream endpoint"));
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(transcript_scroller), messages_box);
    gtk_box_append(GTK_BOX(transcript_card), transcript_scroller);

    gtk_box_append(GTK_BOX(page), hero);
    gtk_box_append(GTK_BOX(page), transcript_card);
    gtk_box_append(GTK_BOX(page), composer_card);

    append_chat_message(ui, "LIARA", "Ich bereite eine persoenliche Begruessung vor ...", TRUE, NULL, NULL);

    g_signal_connect(send_button, "clicked", G_CALLBACK(on_send_chat_clicked), ui);
    
    /* Setup Enter/Shift+Enter handler for chat input */
    ui->chat_input_handler = liara_chat_input_new(
        ui->chat_input,
        on_chat_input_ready,
        ui
    );
    
    return page;
}

static GtkWidget *
build_history_view(LiaraWindow *ui)
{
    GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    GtkWidget *card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    GtkWidget *grid = gtk_grid_new();
    GtkWidget *refresh_button = gtk_button_new_with_label("Refresh History");
    GtkWidget *run_id_entry = gtk_entry_new();
    GtkWidget *history_scroller = gtk_scrolled_window_new();
    GtkWidget *history_box = gtk_list_box_new();

    ui->history_session_entry = GTK_ENTRY(gtk_entry_new());
    ui->history_run_id_entry = GTK_ENTRY(run_id_entry);
    ui->history_limit = GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1, 500, 1));
    ui->history_include_tools = GTK_CHECK_BUTTON(gtk_check_button_new_with_label("Include tool messages"));
    ui->history_scroller = GTK_SCROLLED_WINDOW(history_scroller);
    ui->history_messages_box = history_box;

    gtk_spin_button_set_value(ui->history_limit, 50);
    gtk_check_button_set_active(ui->history_include_tools, ui->client_history_include_tools);
    gtk_editable_set_text(GTK_EDITABLE(ui->history_run_id_entry), "");

    gtk_widget_add_css_class(page, "content-page");
    gtk_widget_add_css_class(card, "surface-card");
    gtk_widget_add_css_class(history_scroller, "chat-transcript");
    gtk_widget_add_css_class(history_box, "chat-messages");
    gtk_grid_set_row_spacing(GTK_GRID(grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(grid), 8);
    gtk_widget_set_vexpand(history_scroller, TRUE);
    gtk_widget_set_hexpand(history_scroller, TRUE);
    gtk_widget_set_vexpand(card, TRUE);
    gtk_widget_set_hexpand(card, TRUE);
    gtk_widget_set_valign(history_box, GTK_ALIGN_START);
    gtk_list_box_set_selection_mode(GTK_LIST_BOX(history_box), GTK_SELECTION_NONE);
    gtk_scrolled_window_set_propagate_natural_width(GTK_SCROLLED_WINDOW(history_scroller), FALSE);
    gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(history_scroller), FALSE);

    if (ui->dev_mode) {
        gtk_grid_attach(GTK_GRID(grid), gtk_label_new("Session"), 0, 0, 1, 1);
        gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->history_session_entry), 1, 0, 1, 1);
        gtk_grid_attach(GTK_GRID(grid), gtk_label_new("Run ID"), 2, 0, 1, 1);
        gtk_grid_attach(GTK_GRID(grid), run_id_entry, 3, 0, 1, 1);
        gtk_grid_attach(GTK_GRID(grid), gtk_label_new("Limit"), 0, 1, 1, 1);
        gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->history_limit), 1, 1, 1, 1);
        gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->history_include_tools), 2, 1, 2, 1);
    }

    gtk_box_append(
        GTK_BOX(card),
        make_panel_title(
            "memory",
            "History",
            ui->dev_mode
                ? "Readable transcript output with GitHub-like monospace detail"
                : "Conversation history for the current session"
        )
    );
    if (ui->dev_mode) {
        gtk_box_append(GTK_BOX(card), grid);
    }
    gtk_box_append(GTK_BOX(card), refresh_button);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(history_scroller), history_box);
    gtk_box_append(GTK_BOX(card), history_scroller);
    gtk_box_append(GTK_BOX(page), card);

    append_history_message(ui, "assistant", "Load a session to view the conversation as bubbles.");

    g_signal_connect(refresh_button, "clicked", G_CALLBACK(on_refresh_history_clicked), ui);
    return page;
}

static GtkWidget *
build_explorer_view(LiaraWindow *ui)
{
    GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    
    /* Create Windows Explorer-style workspace navigator */
    ui->workspace_explorer = liara_workspace_explorer_new("/home/liara/workspace");
    GtkWidget *explorer_widget = liara_workspace_explorer_get_widget(ui->workspace_explorer);
    
    gtk_widget_add_css_class(page, "content-page");
    gtk_widget_set_hexpand(explorer_widget, TRUE);
    gtk_widget_set_vexpand(explorer_widget, TRUE);
    
    gtk_box_append(GTK_BOX(page), explorer_widget);
    
    /* Keep the old explorer fields for backwards compatibility */
    ui->explorer_list_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    ui->explorer_path_entry = GTK_ENTRY(gtk_entry_new());
    ui->explorer_status_label = GTK_LABEL(gtk_label_new("Explorer ready."));
    ui->explorer_preview = gtk_text_view_new();
    
    return page;
}

static GtkWidget *
build_settings_view(LiaraWindow *ui)
{
    GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    GtkWidget *card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    GtkWidget *grid = gtk_grid_new();
    GtkWidget *save_button = gtk_button_new_with_label("Save Client Settings");
    GtkWidget *status_hint = gtk_label_new("Changes are stored in config/lserv.json.");

    ui->settings_default_tokens = GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1, 8192, 1));
    ui->settings_stream_enabled = GTK_CHECK_BUTTON(gtk_check_button_new_with_label("Use streaming chat endpoint (/chat/stream)"));
    ui->settings_history_include_tools = GTK_CHECK_BUTTON(gtk_check_button_new_with_label("Include tool messages by default in history"));
    ui->settings_saved_hint = GTK_LABEL(status_hint);

    gtk_widget_add_css_class(page, "content-page");
    gtk_widget_add_css_class(card, "surface-card");
    gtk_widget_add_css_class(save_button, "suggested-action");
    gtk_grid_set_row_spacing(GTK_GRID(grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(grid), 8);
    gtk_label_set_xalign(GTK_LABEL(status_hint), 0.0f);

    gtk_grid_attach(GTK_GRID(grid), gtk_label_new("Default max tokens"), 0, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->settings_default_tokens), 1, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->settings_stream_enabled), 0, 1, 2, 1);
    gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->settings_history_include_tools), 0, 2, 2, 1);

    gtk_box_append(
        GTK_BOX(card),
        make_panel_title("client", "Settings", "Client-side chat and history behavior")
    );
    gtk_box_append(GTK_BOX(card), grid);
    gtk_box_append(GTK_BOX(card), save_button);
    gtk_box_append(GTK_BOX(card), status_hint);
    gtk_box_append(GTK_BOX(page), card);

    sync_client_settings_to_widgets(ui);
    g_signal_connect(save_button, "clicked", G_CALLBACK(on_apply_client_settings_clicked), ui);
    return page;
}

static GtkWidget *
build_audit_view(LiaraWindow *ui)
{
    GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    GtkWidget *card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    GtkWidget *filter_grid = gtk_grid_new();
    GtkWidget *button_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *summary_button = gtk_button_new_with_label("Load Summary");
    GtkWidget *suspicious_button = gtk_button_new_with_label("Load Suspicious");
    GtkWidget *preset_button = gtk_button_new_with_label("Run Preset");
    GtkWidget *output_view = make_editor_view(&ui->audit_output, "response-view", FALSE);

    ui->audit_limit = GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1, 5000, 1));
    ui->audit_max_items = GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1, 200, 1));
    ui->audit_blocked_only = GTK_CHECK_BUTTON(gtk_check_button_new_with_label("Blocked only"));
    ui->audit_source_entry = GTK_ENTRY(gtk_entry_new());
    ui->audit_risk_entry = GTK_ENTRY(gtk_entry_new());
    ui->audit_family_entry = GTK_ENTRY(gtk_entry_new());
    ui->audit_preset_entry = GTK_ENTRY(gtk_entry_new());

    gtk_spin_button_set_value(ui->audit_limit, 500);
    gtk_spin_button_set_value(ui->audit_max_items, 30);
    gtk_editable_set_text(GTK_EDITABLE(ui->audit_source_entry), "all");
    gtk_editable_set_text(GTK_EDITABLE(ui->audit_risk_entry), "all");
    gtk_editable_set_text(GTK_EDITABLE(ui->audit_family_entry), "all");
    gtk_editable_set_text(GTK_EDITABLE(ui->audit_preset_entry), "top-risk");

    gtk_widget_add_css_class(page, "content-page");
    gtk_widget_add_css_class(card, "surface-card");
    gtk_widget_add_css_class(summary_button, "suggested-action");
    gtk_grid_set_row_spacing(GTK_GRID(filter_grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(filter_grid), 8);

    gtk_grid_attach(GTK_GRID(filter_grid), gtk_label_new("Limit"), 0, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(filter_grid), GTK_WIDGET(ui->audit_limit), 1, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(filter_grid), gtk_label_new("Max items"), 2, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(filter_grid), GTK_WIDGET(ui->audit_max_items), 3, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(filter_grid), GTK_WIDGET(ui->audit_blocked_only), 4, 0, 2, 1);

    gtk_grid_attach(GTK_GRID(filter_grid), gtk_label_new("Source"), 0, 1, 1, 1);
    gtk_grid_attach(GTK_GRID(filter_grid), GTK_WIDGET(ui->audit_source_entry), 1, 1, 1, 1);
    gtk_grid_attach(GTK_GRID(filter_grid), gtk_label_new("Risk"), 2, 1, 1, 1);
    gtk_grid_attach(GTK_GRID(filter_grid), GTK_WIDGET(ui->audit_risk_entry), 3, 1, 1, 1);
    gtk_grid_attach(GTK_GRID(filter_grid), gtk_label_new("Family"), 4, 1, 1, 1);
    gtk_grid_attach(GTK_GRID(filter_grid), GTK_WIDGET(ui->audit_family_entry), 5, 1, 1, 1);

    gtk_grid_attach(GTK_GRID(filter_grid), gtk_label_new("Preset"), 0, 2, 1, 1);
    gtk_grid_attach(GTK_GRID(filter_grid), GTK_WIDGET(ui->audit_preset_entry), 1, 2, 2, 1);

    gtk_box_append(GTK_BOX(button_row), summary_button);
    gtk_box_append(GTK_BOX(button_row), suspicious_button);
    gtk_box_append(GTK_BOX(button_row), preset_button);

    gtk_box_append(
        GTK_BOX(card),
        make_panel_title("audit", "Audit History", "Admin sys-audit endpoints (summary, suspicious, presets)")
    );
    gtk_box_append(GTK_BOX(card), filter_grid);
    gtk_box_append(GTK_BOX(card), button_row);
    gtk_box_append(GTK_BOX(card), output_view);
    gtk_box_append(GTK_BOX(page), card);

    set_text_view_text(ui->audit_output, "Use filters and load sys-audit data. Results are shown as a human-readable operational summary.");

    g_signal_connect(summary_button, "clicked", G_CALLBACK(on_audit_summary_clicked), ui);
    g_signal_connect(suspicious_button, "clicked", G_CALLBACK(on_audit_suspicious_clicked), ui);
    g_signal_connect(preset_button, "clicked", G_CALLBACK(on_audit_preset_clicked), ui);
    return page;
}

static GtkWidget *
build_tools_view(LiaraWindow *ui)
{
    GtkWidget *page = gtk_box_new(GTK_ORIENTATION_VERTICAL, 16);
    GtkWidget *card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    GtkWidget *tool_browser = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget *tool_list_scroller = gtk_scrolled_window_new();
    GtkWidget *details_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget *grid = gtk_grid_new();
    GtkWidget *list_button = gtk_button_new_with_label("List Tools");
    GtkWidget *details_button = gtk_button_new_with_label("Tool Details");
    GtkWidget *invoke_button = gtk_button_new_with_label("Invoke Tool");
    GtkWidget *required_label = gtk_label_new("Required");
    GtkWidget *optional_label = gtk_label_new("Optional");
    GtkWidget *form_label = gtk_label_new("Quick Form");
    GtkWidget *form_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget *params_view = make_editor_view(&ui->tool_params_input, "composer-input", TRUE);
    GtkWidget *output_view = make_editor_view(&ui->tools_output, "response-view", FALSE);
    GtkWidget *button_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);

    ui->tool_list_box = gtk_list_box_new();
    ui->tool_detail_name = GTK_LABEL(gtk_label_new("current_time"));
    ui->tool_detail_description = GTK_LABEL(gtk_label_new("Select a tool and load its metadata."));
    ui->tool_required_params_box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    ui->tool_optional_params_box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    ui->tool_form_box = form_box;
    ui->tool_name_entry = GTK_ENTRY(gtk_entry_new());
    ui->tool_timeout = GTK_SPIN_BUTTON(gtk_spin_button_new_with_range(1, 120, 1));

    gtk_editable_set_text(GTK_EDITABLE(ui->tool_name_entry), "current_time");
    gtk_spin_button_set_value(ui->tool_timeout, 30);
    set_text_view_text(ui->tool_params_input, "{}");

    gtk_widget_add_css_class(page, "content-page");
    gtk_widget_add_css_class(card, "surface-card");
    gtk_widget_add_css_class(tool_browser, "config-card");
    gtk_widget_add_css_class(tool_list_scroller, "chat-transcript");
    gtk_widget_add_css_class(details_card, "config-card");
    gtk_widget_add_css_class(GTK_WIDGET(ui->tool_detail_name), "panel-title");
    gtk_widget_add_css_class(GTK_WIDGET(ui->tool_detail_description), "panel-subtitle");
    gtk_widget_add_css_class(required_label, "eyebrow");
    gtk_widget_add_css_class(optional_label, "eyebrow");
    gtk_widget_add_css_class(form_label, "eyebrow");
    gtk_widget_add_css_class(form_box, "tool-form-box");
    gtk_grid_set_row_spacing(GTK_GRID(grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(grid), 8);
    gtk_label_set_xalign(ui->tool_detail_name, 0.0f);
    gtk_label_set_xalign(ui->tool_detail_description, 0.0f);
    gtk_label_set_wrap(ui->tool_detail_description, TRUE);
    gtk_widget_set_vexpand(tool_list_scroller, TRUE);
    gtk_widget_set_hexpand(tool_list_scroller, TRUE);
    gtk_scrolled_window_set_propagate_natural_width(GTK_SCROLLED_WINDOW(tool_list_scroller), FALSE);
    gtk_scrolled_window_set_propagate_natural_height(GTK_SCROLLED_WINDOW(tool_list_scroller), FALSE);
    gtk_list_box_set_selection_mode(GTK_LIST_BOX(ui->tool_list_box), GTK_SELECTION_BROWSE);
    gtk_box_append(GTK_BOX(ui->tool_required_params_box), create_metadata_chip("No required parameters"));
    gtk_box_append(GTK_BOX(ui->tool_optional_params_box), create_metadata_chip("No optional parameters"));
    gtk_grid_attach(GTK_GRID(grid), gtk_label_new("Tool"), 0, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->tool_name_entry), 1, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), gtk_label_new("Timeout"), 2, 0, 1, 1);
    gtk_grid_attach(GTK_GRID(grid), GTK_WIDGET(ui->tool_timeout), 3, 0, 1, 1);

    gtk_box_append(GTK_BOX(button_row), list_button);
    gtk_box_append(GTK_BOX(button_row), details_button);
    gtk_box_append(GTK_BOX(button_row), invoke_button);

    gtk_box_append(GTK_BOX(card), make_panel_title("tooling", "Tools", "Direct API-backed invocation from the desktop shell"));
    gtk_box_append(GTK_BOX(card), grid);
    gtk_box_append(GTK_BOX(card), button_row);
    gtk_box_append(GTK_BOX(tool_browser), make_panel_title("catalog", "Tool Catalog", "Load and click a tool instead of typing names by hand"));
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(tool_list_scroller), ui->tool_list_box);
    gtk_box_append(GTK_BOX(tool_browser), tool_list_scroller);
    gtk_box_append(GTK_BOX(card), tool_browser);
    gtk_box_append(GTK_BOX(details_card), make_panel_title("metadata", "Tool Details", "Readable summary plus parameter hints from the API"));
    gtk_box_append(GTK_BOX(details_card), GTK_WIDGET(ui->tool_detail_name));
    gtk_box_append(GTK_BOX(details_card), GTK_WIDGET(ui->tool_detail_description));
    gtk_box_append(GTK_BOX(details_card), required_label);
    gtk_box_append(GTK_BOX(details_card), ui->tool_required_params_box);
    gtk_box_append(GTK_BOX(details_card), optional_label);
    gtk_box_append(GTK_BOX(details_card), ui->tool_optional_params_box);
    gtk_box_append(GTK_BOX(details_card), form_label);
    gtk_box_append(GTK_BOX(details_card), form_box);
    gtk_box_append(GTK_BOX(form_box), create_metadata_chip("Load a tool to generate a quick parameter form."));
    gtk_box_append(GTK_BOX(card), details_card);
    gtk_box_append(GTK_BOX(card), gtk_label_new("Parameters JSON"));
    gtk_box_append(GTK_BOX(card), params_view);
    gtk_box_append(GTK_BOX(card), output_view);
    gtk_box_append(GTK_BOX(page), card);

    g_signal_connect(list_button, "clicked", G_CALLBACK(on_list_tools_clicked), ui);
    g_signal_connect(details_button, "clicked", G_CALLBACK(on_tool_details_clicked), ui);
    g_signal_connect(invoke_button, "clicked", G_CALLBACK(on_invoke_tool_clicked), ui);
    g_signal_connect(ui->tool_list_box, "row-activated", G_CALLBACK(on_tool_row_activated), ui);
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
    GtkWidget *snapshot_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 6);
    GtkWidget *health_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget *health_cards = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    GtkWidget *health_summary = gtk_label_new("No health data loaded yet.");
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
    ui->status_health_cards = health_cards;
    ui->status_health_summary = GTK_LABEL(health_summary);
    ui->session_info_session_entry = GTK_ENTRY(gtk_entry_new());
    ui->session_info_user_entry = GTK_ENTRY(gtk_entry_new());
    ui->session_sandbox_entry = GTK_ENTRY(gtk_entry_new());
    ui->session_meta_profile_entry = GTK_ENTRY(gtk_entry_new());
    ui->session_meta_workspace_entry = GTK_ENTRY(gtk_entry_new());
    ui->session_meta_notes_entry = GTK_ENTRY(gtk_entry_new());
    ui->session_snapshot_count = GTK_LABEL(gtk_label_new("Messages: -"));
    ui->session_snapshot_last_run = GTK_LABEL(gtk_label_new("Last run: -"));
    ui->session_snapshot_updated = GTK_LABEL(gtk_label_new("Updated: -"));
    ui->session_snapshot_history = GTK_LABEL(gtk_label_new("History: -"));
    ui->session_snapshot_sandbox = GTK_LABEL(gtk_label_new("Sandbox root: -"));

    gtk_widget_add_css_class(panel, "inspector");
    gtk_widget_add_css_class(config_card, "config-card");
    gtk_widget_add_css_class(session_card, "config-card");
    gtk_widget_add_css_class(snapshot_card, "config-card");
    gtk_widget_add_css_class(health_card, "config-card");
    gtk_widget_add_css_class(health_cards, "health-cards");
    gtk_widget_add_css_class(health_summary, "health-summary");
    gtk_widget_add_css_class(apply_button, "suggested-action");
    gtk_label_set_xalign(GTK_LABEL(health_summary), 0.0f);
    gtk_grid_set_row_spacing(GTK_GRID(session_grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(session_grid), 8);
    gtk_grid_set_row_spacing(GTK_GRID(config_grid), 8);
    gtk_grid_set_column_spacing(GTK_GRID(config_grid), 8);
    gtk_label_set_xalign(ui->api_endpoint_hint, 0.0f);
    gtk_label_set_ellipsize(ui->api_endpoint_hint, PANGO_ELLIPSIZE_END);
    gtk_label_set_max_width_chars(ui->api_endpoint_hint, 44);

    gtk_editable_set_text(GTK_EDITABLE(ui->api_base_url_entry), "http://127.0.0.1:8010");
    gtk_editable_set_text(GTK_EDITABLE(ui->api_host_entry), "127.0.0.1");
    gtk_spin_button_set_value(ui->api_port_spin, 8010);

    gtk_box_append(
        GTK_BOX(panel),
        make_panel_title(
            "system",
            "Status",
            ui->dev_mode
                ? "API and memory backends in one glance"
                : "Live service and backend health"
        )
    );
    if (ui->dev_mode) {
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
        gtk_grid_attach(GTK_GRID(session_grid), gtk_label_new("Profile"), 0, 3, 1, 1);
        gtk_grid_attach(GTK_GRID(session_grid), GTK_WIDGET(ui->session_meta_profile_entry), 1, 3, 1, 1);
        gtk_grid_attach(GTK_GRID(session_grid), gtk_label_new("Workspace"), 0, 4, 1, 1);
        gtk_grid_attach(GTK_GRID(session_grid), GTK_WIDGET(ui->session_meta_workspace_entry), 1, 4, 1, 1);
        gtk_grid_attach(GTK_GRID(session_grid), gtk_label_new("Notes"), 0, 5, 1, 1);
        gtk_grid_attach(GTK_GRID(session_grid), GTK_WIDGET(ui->session_meta_notes_entry), 1, 5, 1, 1);
        gtk_box_append(GTK_BOX(session_card), session_grid);
        gtk_box_append(GTK_BOX(session_button_row), load_session_button);
        gtk_box_append(GTK_BOX(session_button_row), save_session_button);
        gtk_box_append(GTK_BOX(session_card), session_button_row);
        gtk_box_append(GTK_BOX(snapshot_card), make_panel_title("snapshot", "Session Snapshot", "Live counters from GET /session"));
        gtk_label_set_xalign(ui->session_snapshot_count, 0.0f);
        gtk_label_set_xalign(ui->session_snapshot_last_run, 0.0f);
        gtk_label_set_xalign(ui->session_snapshot_updated, 0.0f);
        gtk_label_set_xalign(ui->session_snapshot_history, 0.0f);
        gtk_label_set_xalign(ui->session_snapshot_sandbox, 0.0f);
        gtk_box_append(GTK_BOX(snapshot_card), GTK_WIDGET(ui->session_snapshot_count));
        gtk_box_append(GTK_BOX(snapshot_card), GTK_WIDGET(ui->session_snapshot_last_run));
        gtk_box_append(GTK_BOX(snapshot_card), GTK_WIDGET(ui->session_snapshot_updated));
        gtk_box_append(GTK_BOX(snapshot_card), GTK_WIDGET(ui->session_snapshot_history));
        gtk_box_append(GTK_BOX(snapshot_card), GTK_WIDGET(ui->session_snapshot_sandbox));
        gtk_box_append(GTK_BOX(panel), session_card);
        gtk_box_append(GTK_BOX(panel), snapshot_card);
    }
    gtk_box_append(GTK_BOX(health_card), make_panel_title("runtime", "Health Overview", "Live service and backend state at a glance"));
    gtk_box_append(GTK_BOX(button_row), health_button);
    gtk_box_append(GTK_BOX(button_row), backends_button);
    gtk_box_append(GTK_BOX(health_card), button_row);
    gtk_box_append(GTK_BOX(health_card), health_summary);
    gtk_box_append(GTK_BOX(health_card), health_cards);
    gtk_box_append(GTK_BOX(panel), health_card);
    if (ui->dev_mode) {
        gtk_box_append(GTK_BOX(panel), output_view);
    }

    g_signal_connect(health_button, "clicked", G_CALLBACK(on_health_clicked), ui);
    g_signal_connect(backends_button, "clicked", G_CALLBACK(on_health_backends_clicked), ui);
    g_signal_connect(apply_button, "clicked", G_CALLBACK(on_apply_connection_clicked), ui);
    g_signal_connect(load_session_button, "clicked", G_CALLBACK(on_load_session_clicked), ui);
    g_signal_connect(save_session_button, "clicked", G_CALLBACK(on_save_session_clicked), ui);
    return wrap_side_panel(panel);
}

static GtkWidget *
build_history_side_panel(LiaraWindow *ui)
{
    GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    (void) ui;
    gtk_widget_add_css_class(panel, "inspector");
    gtk_box_append(GTK_BOX(panel), make_panel_title("context", "History Notes", "Use the history page to inspect current session state"));
    gtk_box_append(GTK_BOX(panel), make_tip_label("Tip: keep the same session id in Chat and History to replay the current conversation."));
    return wrap_side_panel(panel);
}

static GtkWidget *
build_explorer_side_panel(LiaraWindow *ui)
{
    GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    (void) ui;
    gtk_widget_add_css_class(panel, "inspector");
    gtk_box_append(GTK_BOX(panel), make_panel_title("filesystem", "Explorer Notes", "Safe browsing over /sys with command policy enforcement"));
    gtk_box_append(GTK_BOX(panel), make_tip_label("Tip: Home and Workspace buttons jump to /home/liara or /home/liara/workspace quickly."));
    gtk_box_append(GTK_BOX(panel), make_tip_label("Tip: sensitive paths like /home/liara/.ssh remain blocked by policy."));
    return wrap_side_panel(panel);
}

static GtkWidget *
build_tools_side_panel(LiaraWindow *ui)
{
    GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    (void) ui;
    gtk_widget_add_css_class(panel, "inspector");
    gtk_box_append(GTK_BOX(panel), make_panel_title("developer", "Tool Console", "Manual tool execution with JSON parameters"));
    gtk_box_append(GTK_BOX(panel), make_tip_label("Tip: the output panel shows the raw API response to keep debugging honest."));
    return wrap_side_panel(panel);
}

static GtkWidget *
build_settings_side_panel(LiaraWindow *ui)
{
    GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    (void) ui;
    gtk_widget_add_css_class(panel, "inspector");
    gtk_box_append(GTK_BOX(panel), make_panel_title("settings", "Client Settings", "Persisted locally in lserv.json"));
    gtk_box_append(GTK_BOX(panel), make_tip_label("Tip: disable streaming when debugging basic chat payloads or flaky SSE networks."));
    gtk_box_append(GTK_BOX(panel), make_tip_label("Tip: default max tokens is copied into the Chat composer when the app starts."));
    return wrap_side_panel(panel);
}

static GtkWidget *
build_audit_side_panel(LiaraWindow *ui)
{
    GtkWidget *panel = gtk_box_new(GTK_ORIENTATION_VERTICAL, 12);
    (void) ui;
    gtk_widget_add_css_class(panel, "inspector");
    gtk_box_append(GTK_BOX(panel), make_panel_title("audit", "Audit Notes", "Live operational audit with risk filters"));
    gtk_box_append(GTK_BOX(panel), make_tip_label("Tip: source/risk/family support values like all, orchestrator, high, network."));
    gtk_box_append(GTK_BOX(panel), make_tip_label("Tip: presets include top-risk, blocked-only, orchestrator-network-risk."));
    return wrap_side_panel(panel);
}

static const char *
side_panel_for_main_view(const char *main_name)
{
    if (g_strcmp0(main_name, "history") == 0) {
        return "history-side";
    }
    if (g_strcmp0(main_name, "explorer") == 0) {
        return "explorer-side";
    }
    if (g_strcmp0(main_name, "tools") == 0) {
        return "tools";
    }
    if (g_strcmp0(main_name, "settings") == 0) {
        return "settings-side";
    }
    if (g_strcmp0(main_name, "audit") == 0) {
        return "audit-side";
    }
    if (g_strcmp0(main_name, "status") == 0) {
        return "status";
    }
    return "status";
}

static void
on_action_view(GSimpleAction *action, GVariant *parameter, gpointer user_data)
{
    LiaraWindow *ui = user_data;
    const char *main_name;
    const char *side_name;

    (void) action;

    if (parameter == NULL) {
        return;
    }

    main_name = g_variant_get_string(parameter, NULL);
    side_name = side_panel_for_main_view(main_name);

    if ((g_strcmp0(main_name, "tools") == 0 || g_strcmp0(main_name, "audit") == 0) && !ui->dev_mode) {
        return;
    }

    if (g_strcmp0(main_name, "status") == 0) {
        switch_view(ui, "chat", "status");
        return;
    }

    if (g_strcmp0(main_name, "explorer") == 0 && ui->explorer_path_entry != NULL) {
        const char *path = gtk_editable_get_text(GTK_EDITABLE(ui->explorer_path_entry));
        request_explorer_list(ui, path);
    }

    switch_view(ui, main_name, side_name);
}

static void
on_action_send_chat(GSimpleAction *action, GVariant *parameter, gpointer user_data)
{
    (void) action;
    (void) parameter;
    on_send_chat_clicked(NULL, user_data);
}

static void
on_action_clear_chat(GSimpleAction *action, GVariant *parameter, gpointer user_data)
{
    LiaraWindow *ui = user_data;

    (void) action;
    (void) parameter;

    clear_box_children(ui->chat_messages_box);
    append_chat_message(ui, "LIARA", "Transcript cleared.", TRUE, NULL, NULL);
    set_text_view_text(ui->chat_input, "");
}

static GtkWidget *
build_menu_bar(LiaraWindow *ui)
{
    GtkWidget *menubar;
    g_autoptr(GMenu) root = g_menu_new();
    g_autoptr(GMenu) nav = g_menu_new();
    g_autoptr(GMenu) chat = g_menu_new();
    GSimpleAction *view_action;
    GSimpleAction *send_action;
    GSimpleAction *clear_action;

    view_action = g_simple_action_new("view", G_VARIANT_TYPE_STRING);
    g_signal_connect(view_action, "activate", G_CALLBACK(on_action_view), ui);
    g_action_map_add_action(G_ACTION_MAP(ui->window), G_ACTION(view_action));

    send_action = g_simple_action_new("send-chat", NULL);
    g_signal_connect(send_action, "activate", G_CALLBACK(on_action_send_chat), ui);
    g_action_map_add_action(G_ACTION_MAP(ui->window), G_ACTION(send_action));

    clear_action = g_simple_action_new("clear-chat", NULL);
    g_signal_connect(clear_action, "activate", G_CALLBACK(on_action_clear_chat), ui);
    g_action_map_add_action(G_ACTION_MAP(ui->window), G_ACTION(clear_action));

    g_menu_append(nav, "Chat", "win.view::chat");
    g_menu_append(nav, "History", "win.view::history");
    g_menu_append(nav, "Explorer", "win.view::explorer");
    if (ui->dev_mode) {
        g_menu_append(nav, "Tools", "win.view::tools");
        g_menu_append(nav, "Audit", "win.view::audit");
    }
    g_menu_append(nav, "Status", "win.view::status");
    g_menu_append(nav, "Settings", "win.view::settings");

    g_menu_append(chat, "Send Prompt", "win.send-chat");
    g_menu_append(chat, "Clear Transcript", "win.clear-chat");

    g_menu_append_section(root, "Navigate", G_MENU_MODEL(nav));
    g_menu_append_section(root, "Chat", G_MENU_MODEL(chat));

    menubar = gtk_popover_menu_bar_new_from_model(G_MENU_MODEL(root));
    gtk_widget_add_css_class(menubar, "app-menubar");
    return menubar;
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
liara_window_new(GtkApplication *app, gboolean dev_mode)
{
    LiaraWindow *ui = g_new0(LiaraWindow, 1);
    GtkWidget *window = gtk_application_window_new(app);
    GtkWidget *root = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    GtkWidget *menu_bar;
    GtkWidget *content_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    GtkWidget *sidebar;
    GtkWidget *center = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    GtkWidget *inspector_shell = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    GtkWidget *inspector_toggle_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    GtkWidget *inspector_toggle_button = gtk_button_new_with_label("<");
    GtkWidget *inspector_revealer = gtk_revealer_new();

    ui->app = app;
    ui->window = window;
    ui->dev_mode = dev_mode;
    ui->api = liara_api_new("http://127.0.0.1:8010");
    ui->config_path = build_default_config_path();
    ui->client_stream_enabled = TRUE;
    ui->client_default_max_tokens = 2048;
    ui->client_history_include_tools = TRUE;
    ui->client_stream_watchdog_seconds = 120;

    ui->main_stack = GTK_STACK(gtk_stack_new());
    ui->side_stack = GTK_STACK(gtk_stack_new());
    ui->inspector_revealer = GTK_REVEALER(inspector_revealer);
    ui->inspector_toggle_button = inspector_toggle_button;

    sidebar = build_sidebar(ui);

    gtk_window_set_title(GTK_WINDOW(window), ui->dev_mode ? "WMTool-Liara [DEV]" : "WMTool-Liara");
    gtk_window_set_default_size(GTK_WINDOW(window), 1440, 900);
    gtk_window_set_child(GTK_WINDOW(window), root);

    gtk_widget_add_css_class(window, "liara-window");
    gtk_widget_add_css_class(center, "center-shell");
    gtk_widget_add_css_class(inspector_shell, "inspector-shell");
    gtk_widget_add_css_class(inspector_toggle_button, "inspector-toggle");
    gtk_window_set_resizable(GTK_WINDOW(window), TRUE);
    gtk_widget_set_size_request(window, 1120, 700);
    gtk_widget_set_hexpand(root, TRUE);
    gtk_widget_set_vexpand(root, TRUE);
    gtk_widget_set_hexpand(content_row, TRUE);
    gtk_widget_set_vexpand(content_row, TRUE);
    gtk_widget_set_hexpand(center, TRUE);
    gtk_widget_set_vexpand(center, TRUE);
    gtk_widget_set_hexpand(sidebar, FALSE);
    gtk_widget_set_hexpand(inspector_shell, FALSE);
    gtk_widget_set_vexpand(inspector_shell, TRUE);
    gtk_widget_set_hexpand(GTK_WIDGET(ui->main_stack), TRUE);
    gtk_widget_set_vexpand(GTK_WIDGET(ui->main_stack), TRUE);
    gtk_widget_set_size_request(sidebar, 240, -1);
    gtk_widget_set_size_request(inspector_shell, 356, -1);
    gtk_widget_set_halign(inspector_toggle_row, GTK_ALIGN_END);
    gtk_revealer_set_transition_type(GTK_REVEALER(inspector_revealer), GTK_REVEALER_TRANSITION_TYPE_SLIDE_LEFT);
    gtk_revealer_set_transition_duration(GTK_REVEALER(inspector_revealer), 180);
    gtk_revealer_set_reveal_child(GTK_REVEALER(inspector_revealer), TRUE);
    gtk_widget_set_hexpand(inspector_revealer, TRUE);
    gtk_widget_set_vexpand(inspector_revealer, TRUE);

    gtk_stack_add_named(ui->main_stack, build_chat_view(ui), "chat");
    gtk_stack_add_named(ui->main_stack, build_history_view(ui), "history");
    gtk_stack_add_named(ui->main_stack, build_explorer_view(ui), "explorer");
    gtk_stack_add_named(ui->main_stack, build_settings_view(ui), "settings");
    if (ui->dev_mode) {
        gtk_stack_add_named(ui->main_stack, build_audit_view(ui), "audit");
        gtk_stack_add_named(ui->main_stack, build_tools_view(ui), "tools");
    }
    gtk_stack_set_visible_child_name(ui->main_stack, "chat");

    gtk_stack_add_named(ui->side_stack, build_status_panel(ui), "status");
    gtk_stack_add_named(ui->side_stack, build_history_side_panel(ui), "history-side");
    gtk_stack_add_named(ui->side_stack, build_explorer_side_panel(ui), "explorer-side");
    gtk_stack_add_named(ui->side_stack, build_settings_side_panel(ui), "settings-side");
    if (ui->dev_mode) {
        gtk_stack_add_named(ui->side_stack, build_audit_side_panel(ui), "audit-side");
        gtk_stack_add_named(ui->side_stack, build_tools_side_panel(ui), "tools");
    }
    gtk_stack_set_visible_child_name(ui->side_stack, "status");
    gtk_widget_set_hexpand(GTK_WIDGET(ui->side_stack), TRUE);
    gtk_widget_set_vexpand(GTK_WIDGET(ui->side_stack), TRUE);

    menu_bar = build_menu_bar(ui);

    gtk_box_append(GTK_BOX(center), GTK_WIDGET(ui->main_stack));
    gtk_box_append(GTK_BOX(inspector_toggle_row), inspector_toggle_button);
    gtk_revealer_set_child(GTK_REVEALER(inspector_revealer), GTK_WIDGET(ui->side_stack));
    gtk_box_append(GTK_BOX(inspector_shell), inspector_toggle_row);
    gtk_box_append(GTK_BOX(inspector_shell), inspector_revealer);

    gtk_box_append(GTK_BOX(content_row), sidebar);
    gtk_box_append(GTK_BOX(content_row), center);
    gtk_box_append(GTK_BOX(content_row), inspector_shell);
    gtk_box_append(GTK_BOX(root), menu_bar);
    gtk_box_append(GTK_BOX(root), content_row);

    apply_windows_identity_defaults(ui);
    load_connection_config(ui);
    request_startup_greeting(ui);
    g_signal_connect(inspector_toggle_button, "clicked", G_CALLBACK(on_toggle_inspector_clicked), ui);
    g_signal_connect(window, "destroy", G_CALLBACK(on_window_destroy), ui);
    return window;
}
