#include <gtk/gtk.h>
#include <libsoup/soup.h>
#include <json-glib/json-glib.h>

#include <stdio.h>
#include <stdarg.h>
#include <signal.h>
#include <stdlib.h>
#include <errno.h>
#include <glib/gstdio.h>

#ifdef G_OS_WIN32
#include <windows.h>
#include <gdk/win32/gdkwin32.h>
#endif

#define SERVICE_COUNT 3
#define POLL_INTERVAL_SECONDS 3
#define STOP_GRACE_MILLISECONDS 1500
#define HEALTH_FAIL_DEBOUNCE_THRESHOLD 2

#ifndef LIARA_SERVER_MANAGER_VERSION
#define LIARA_SERVER_MANAGER_VERSION "dev"
#endif

typedef enum {
    LOG_LEVEL_DEBUG = 0,
    LOG_LEVEL_INFO = 1,
    LOG_LEVEL_WARN = 2,
    LOG_LEVEL_ERROR = 3
} LogLevel;

typedef enum {
    RUN_SOURCE_STOPPED = 0,
    RUN_SOURCE_OWNED_SUBPROCESS = 1,
    RUN_SOURCE_CACHED_PID = 2
} RunSource;

typedef struct {
    const char *key;
    const char *name;
    const char *health_url;
    const char *process_match_token;
    gchar **argv;
    gchar *cwd;
    GSubprocess *process;
    GDataInputStream *log_stream;
    gboolean exit_logged;
    gboolean restart_pending;
    guint stop_timeout_source_id;
    guint health_fail_streak;
    gint tracked_pid;
    gchar *pid_file_path;
    RunSource run_source;

    GtkLabel *process_label;
    GtkLabel *pid_label;
    GtkLabel *health_label;
} ManagedService;

typedef struct {
    GtkApplication *application;
    GtkWindow *window;
    GtkTextBuffer *log_buffer;
    SoupSession *http_session;
    gchar *project_root;
    gchar *python_exe;
    gchar *config_path;
    gchar *env_file_path;
    gchar *log_file_path;
    gboolean autostart_enabled;
    gboolean restart_on_nonzero;
    guint start_delay_ms;
    LogLevel log_level;
    gboolean is_shutting_down;
    guint present_attempts;
    ManagedService services[SERVICE_COUNT];
    guint present_source_id;
    guint poll_once_source_id;
    guint poll_source_id;
    guint sequential_start_source_id;
} AppState;

typedef struct {
    AppState *app;
    ManagedService *service;
} LogReadContext;

typedef struct {
    AppState *app;
    ManagedService *service;
} StopTimeoutContext;

typedef struct {
    AppState *app;
    ManagedService *service;
    SoupMessage *message;
} HealthProbeContext;

typedef struct {
    AppState *app;
    int next_index;
} SequentialStartContext;

static void append_log_level(AppState *app, LogLevel level, const char *format, ...);
static void service_start(AppState *app, ManagedService *service);
static void service_stop(AppState *app, ManagedService *service);
static void service_restart(AppState *app, ManagedService *service);
static void start_all_sequential(AppState *app);

static gboolean
ensure_window_foreground(gpointer user_data)
{
    AppState *app = user_data;
    if (app != NULL) {
        app->present_source_id = 0;
    }

    if (app == NULL || app->window == NULL) {
        return G_SOURCE_REMOVE;
    }

    gtk_widget_set_visible(GTK_WIDGET(app->window), TRUE);
    gtk_window_present(app->window);

#ifdef G_OS_WIN32
    GdkSurface *surface = gtk_native_get_surface(GTK_NATIVE(app->window));
    if (surface != NULL) {
        HWND hwnd = gdk_win32_surface_get_handle(surface);
        if (hwnd != NULL) {
            RECT work_area = {0};
            int width = 1180;
            int height = 760;
            int x = 80;
            int y = 80;

            SystemParametersInfoW(SPI_GETWORKAREA, 0, &work_area, 0);
            if ((work_area.right - work_area.left) > width) {
                x = work_area.left + ((work_area.right - work_area.left) - width) / 2;
            }
            if ((work_area.bottom - work_area.top) > height) {
                y = work_area.top + ((work_area.bottom - work_area.top) - height) / 2;
            }

            ShowWindow(hwnd, SW_SHOWNORMAL);
            SetWindowPos(hwnd, HWND_TOPMOST, x, y, width, height, SWP_SHOWWINDOW);
            SetWindowPos(hwnd, HWND_NOTOPMOST, x, y, width, height, SWP_SHOWWINDOW);
            SetForegroundWindow(hwnd);
            BringWindowToTop(hwnd);
            app->present_attempts++;
            if (app->present_attempts < 6) {
                append_log_level(app, LOG_LEVEL_DEBUG, "[system] present attempt %u hwnd=0x%p", app->present_attempts, hwnd);
                return G_SOURCE_CONTINUE;
            }
        }
        append_log_level(app, LOG_LEVEL_DEBUG, "[system] present attempt %u surface ready, hwnd missing", app->present_attempts + 1);
    } else {
        app->present_attempts++;
        append_log_level(app, LOG_LEVEL_DEBUG, "[system] present attempt %u surface not ready", app->present_attempts);
        if (app->present_attempts < 6) {
            return G_SOURCE_CONTINUE;
        }
    }
#endif

    return G_SOURCE_REMOVE;
}

static const gchar *
log_level_to_string(LogLevel level)
{
    switch (level) {
        case LOG_LEVEL_DEBUG:
            return "DEBUG";
        case LOG_LEVEL_INFO:
            return "INFO";
        case LOG_LEVEL_WARN:
            return "WARN";
        case LOG_LEVEL_ERROR:
            return "ERROR";
        default:
            return "INFO";
    }
}

static LogLevel
parse_log_level(const gchar *value)
{
    if (value == NULL || value[0] == '\0') {
        return LOG_LEVEL_INFO;
    }

    if (g_ascii_strcasecmp(value, "debug") == 0) {
        return LOG_LEVEL_DEBUG;
    }
    if (g_ascii_strcasecmp(value, "info") == 0) {
        return LOG_LEVEL_INFO;
    }
    if (g_ascii_strcasecmp(value, "warn") == 0 || g_ascii_strcasecmp(value, "warning") == 0) {
        return LOG_LEVEL_WARN;
    }
    if (g_ascii_strcasecmp(value, "error") == 0) {
        return LOG_LEVEL_ERROR;
    }

    return LOG_LEVEL_INFO;
}

static gchar *
build_default_env_file_path(const gchar *project_root)
{
    g_autofree gchar *parent = NULL;

    if (project_root == NULL || project_root[0] == '\0') {
        return NULL;
    }

    parent = g_path_get_dirname(project_root);
    return g_build_filename(parent, ".env", NULL);
}

static gchar *
build_default_log_file_path(void)
{
    const gchar *configured = g_getenv("LIARA_SERVER_MANAGER_LOG");

    if (configured != NULL && configured[0] != '\0') {
        return g_strdup(configured);
    }

    g_autofree gchar *cwd = g_get_current_dir();
    return g_build_filename(cwd, "logs", "ui", "server-manager.log", NULL);
}

static void
ensure_log_file_exists(AppState *app)
{
    g_autofree gchar *log_dir = NULL;

    if (app->log_file_path == NULL || app->log_file_path[0] == '\0') {
        return;
    }

    log_dir = g_path_get_dirname(app->log_file_path);
    g_mkdir_with_parents(log_dir, 0755);
    if (!g_file_test(app->log_file_path, G_FILE_TEST_EXISTS)) {
        g_file_set_contents(app->log_file_path, "", 0, NULL);
    }
}

static gchar *
build_server_manager_config_path(void)
{
    const gchar *configured = g_getenv("LIARA_SERVER_MANAGER_CONFIG");

    if (configured != NULL && configured[0] != '\0') {
        return g_strdup(configured);
    }

    g_autofree gchar *cwd = g_get_current_dir();
    return g_build_filename(cwd, "config", "server-manager.json", NULL);
}

static void
save_server_manager_config(AppState *app)
{
    g_autoptr(JsonBuilder) builder = json_builder_new();
    g_autoptr(JsonGenerator) generator = json_generator_new();
    g_autoptr(JsonNode) root = NULL;
    g_autofree gchar *json = NULL;
    g_autofree gchar *config_dir = NULL;

    if (app->config_path == NULL || app->config_path[0] == '\0') {
        return;
    }

    config_dir = g_path_get_dirname(app->config_path);
    g_mkdir_with_parents(config_dir, 0755);

    json_builder_begin_object(builder);
    json_builder_set_member_name(builder, "autostart");
    json_builder_add_boolean_value(builder, app->autostart_enabled);
    json_builder_set_member_name(builder, "env_file");
    json_builder_add_string_value(builder, app->env_file_path != NULL ? app->env_file_path : "");
    json_builder_set_member_name(builder, "restart_on_nonzero");
    json_builder_add_boolean_value(builder, app->restart_on_nonzero);
    json_builder_set_member_name(builder, "start_delay_ms");
    json_builder_add_int_value(builder, (gint64) app->start_delay_ms);
    json_builder_set_member_name(builder, "log_level");
    json_builder_add_string_value(builder, log_level_to_string(app->log_level));
    json_builder_end_object(builder);

    root = json_builder_get_root(builder);
    json_generator_set_root(generator, root);
    json = json_generator_to_data(generator, NULL);
    g_file_set_contents(app->config_path, json, -1, NULL);
}

static void
load_server_manager_config(AppState *app)
{
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonNode *root;
    JsonObject *object;

    app->config_path = build_server_manager_config_path();
    app->autostart_enabled = FALSE;
    app->restart_on_nonzero = FALSE;
    app->start_delay_ms = 1500;
    app->log_level = LOG_LEVEL_INFO;
    g_clear_pointer(&app->env_file_path, g_free);
    app->env_file_path = build_default_env_file_path(app->project_root);

    if (!g_file_test(app->config_path, G_FILE_TEST_EXISTS)) {
        save_server_manager_config(app);
        return;
    }

    if (!json_parser_load_from_file(parser, app->config_path, NULL)) {
        save_server_manager_config(app);
        return;
    }

    root = json_parser_get_root(parser);
    if (root == NULL || !JSON_NODE_HOLDS_OBJECT(root)) {
        save_server_manager_config(app);
        return;
    }

    object = json_node_get_object(root);
    if (json_object_has_member(object, "autostart")) {
        JsonNode *node = json_object_get_member(object, "autostart");
        if (node != NULL && JSON_NODE_HOLDS_VALUE(node)) {
            app->autostart_enabled = json_node_get_boolean(node);
        }
    }
    if (json_object_has_member(object, "env_file")) {
        JsonNode *node = json_object_get_member(object, "env_file");
        if (node != NULL && JSON_NODE_HOLDS_VALUE(node)) {
            const gchar *env_file = json_node_get_string(node);
            if (env_file != NULL && env_file[0] != '\0') {
                g_clear_pointer(&app->env_file_path, g_free);
                app->env_file_path = g_strdup(env_file);
            }
        }
    }
    if (json_object_has_member(object, "restart_on_nonzero")) {
        JsonNode *node = json_object_get_member(object, "restart_on_nonzero");
        if (node != NULL && JSON_NODE_HOLDS_VALUE(node)) {
            app->restart_on_nonzero = json_node_get_boolean(node);
        }
    }
    if (json_object_has_member(object, "start_delay_ms")) {
        JsonNode *node = json_object_get_member(object, "start_delay_ms");
        if (node != NULL && JSON_NODE_HOLDS_VALUE(node)) {
            gint64 delay = json_node_get_int(node);
            app->start_delay_ms = (guint) MAX(0, delay);
        }
    }
    if (json_object_has_member(object, "log_level")) {
        JsonNode *node = json_object_get_member(object, "log_level");
        if (node != NULL && JSON_NODE_HOLDS_VALUE(node)) {
            const gchar *log_level = json_node_get_string(node);
            app->log_level = parse_log_level(log_level);
        }
    }
}

static gchar *
trimmed_copy(const gchar *value)
{
    g_autofree gchar *copy = g_strdup(value != NULL ? value : "");
    return g_strdup(g_strstrip(copy));
}

static gchar *
normalize_env_value(const gchar *raw_value)
{
    g_autofree gchar *value = trimmed_copy(raw_value);
    gsize len = strlen(value);

    if (len >= 2) {
        if ((value[0] == '"' && value[len - 1] == '"') || (value[0] == '\'' && value[len - 1] == '\'')) {
            value[len - 1] = '\0';
            return g_strdup(value + 1);
        }
    }

    return g_strdup(value);
}

static void
apply_env_file_to_launcher(AppState *app, GSubprocessLauncher *launcher)
{
    g_autofree gchar *content = NULL;
    gsize length = 0;
    g_auto(GStrv) lines = NULL;

    if (app->env_file_path == NULL || app->env_file_path[0] == '\0') {
        return;
    }

    if (!g_file_test(app->env_file_path, G_FILE_TEST_IS_REGULAR)) {
        append_log_level(app, LOG_LEVEL_WARN, "[system] env file not found: %s", app->env_file_path);
        return;
    }

    if (!g_file_get_contents(app->env_file_path, &content, &length, NULL)) {
        append_log_level(app, LOG_LEVEL_ERROR, "[system] env file could not be read: %s", app->env_file_path);
        return;
    }

    lines = g_strsplit(content, "\n", -1);
    for (guint i = 0; lines[i] != NULL; i++) {
        g_autofree gchar *line = trimmed_copy(lines[i]);
        gchar *effective_line = line;
        gchar *equals = NULL;
        g_autofree gchar *key = NULL;
        g_autofree gchar *value = NULL;

        if (line[0] == '\0' || line[0] == '#') {
            continue;
        }
        if (g_str_has_prefix(line, "export ")) {
            effective_line = g_strstrip(line + 7);
        }

        equals = strchr(effective_line, '=');
        if (equals == NULL) {
            continue;
        }

        *equals = '\0';
        key = trimmed_copy(effective_line);
        value = normalize_env_value(equals + 1);

        if (key[0] == '\0') {
            continue;
        }

        g_subprocess_launcher_setenv(launcher, key, value, TRUE);
    }
}

static void
load_server_manager_css(void)
{
    GtkCssProvider *provider = gtk_css_provider_new();
    const char *css =
        "window.server-manager-window {"
        "  background: linear-gradient(180deg, #f6f8fb 0%, #eef2f7 100%);"
        "  color: #1f2328;"
        "}"
        ".sm-shell {"
        "  padding: 20px;"
        "}"
        ".sm-card {"
        "  background: rgba(255,255,255,0.84);"
        "  border: 1px solid rgba(31,35,40,0.08);"
        "  border-radius: 14px;"
        "  padding: 16px;"
        "  box-shadow: 0 16px 40px rgba(15,23,42,0.08);"
        "}"
        ".sm-title {"
        "  color: #111827;"
        "  font-size: 26px;"
        "  font-weight: 800;"
        "}"
        ".sm-subtitle {"
        "  color: #57606a;"
        "  font-size: 13px;"
        "}"
        ".sm-eyebrow {"
        "  color: #0ea5b7;"
        "  text-transform: uppercase;"
        "  letter-spacing: 0.14em;"
        "  font-size: 11px;"
        "  font-weight: 800;"
        "}"
        ".sm-service-name {"
        "  color: #111827;"
        "  font-size: 17px;"
        "  font-weight: 800;"
        "}"
        ".sm-service-url {"
        "  color: #64748b;"
        "  font-size: 12px;"
        "}"
        ".sm-badge {"
        "  padding: 4px 8px;"
        "  border-radius: 999px;"
        "  background: rgba(15,23,42,0.04);"
        "  border: 1px solid rgba(15,23,42,0.08);"
        "  color: #334155;"
        "  font-size: 11px;"
        "  font-weight: 700;"
        "}"
        ".sm-log-view {"
        "  border-radius: 10px;"
        "  background: #ffffff;"
        "  border: 1px solid rgba(31,35,40,0.08);"
        "}"
        ".sm-primary {"
        "  min-height: 32px;"
        "  border-radius: 8px;"
        "  background: linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%);"
        "  color: white;"
        "  border: 0;"
        "  padding: 6px 12px;"
        "}"
        "button {"
        "  min-height: 30px;"
        "  border-radius: 8px;"
        "  padding: 5px 10px;"
        "}";

    gtk_css_provider_load_from_string(provider, css);
    gtk_style_context_add_provider_for_display(
        gdk_display_get_default(),
        GTK_STYLE_PROVIDER(provider),
        GTK_STYLE_PROVIDER_PRIORITY_APPLICATION
    );
    g_object_unref(provider);
}

static gboolean
directory_has_services(const gchar *path)
{
    g_autofree gchar *services_path = g_build_filename(path, "services", NULL);
    return g_file_test(services_path, G_FILE_TEST_IS_DIR);
}

static gchar *
find_project_root(void)
{
    const gchar *env_root = g_getenv("LIARA_PROJECT_ROOT");

    if (env_root != NULL && directory_has_services(env_root)) {
        return g_strdup(env_root);
    }

    g_autofree gchar *cwd = g_get_current_dir();
    if (directory_has_services(cwd)) {
        return g_steal_pointer(&cwd);
    }

    g_autofree gchar *probe = g_strdup(cwd);
    for (int depth = 0; depth < 8; depth++) {
        if (directory_has_services(probe)) {
            return g_strdup(probe);
        }
        g_autofree gchar *parent = g_path_get_dirname(probe);
        if (g_strcmp0(parent, probe) == 0) {
            break;
        }
        g_free(probe);
        probe = g_strdup(parent);
    }

    return g_steal_pointer(&cwd);
}

static gchar *
resolve_python_executable(const gchar *project_root)
{
    g_autofree gchar *parent = g_path_get_dirname(project_root);
    g_autofree gchar *cand0 = g_build_filename(project_root, ".venv", "Scripts", "python.exe", NULL);
    g_autofree gchar *cand1 = g_build_filename(parent, ".venv", "Scripts", "python.exe", NULL);
    g_autofree gchar *cand2 = g_find_program_in_path("python");
    g_autofree gchar *cand3 = g_find_program_in_path("python3");

    if (cand0 != NULL && g_file_test(cand0, G_FILE_TEST_IS_REGULAR)) {
        return g_strdup(cand0);
    }
    if (cand1 != NULL && g_file_test(cand1, G_FILE_TEST_IS_REGULAR)) {
        return g_strdup(cand1);
    }
    if (cand2 != NULL && g_file_test(cand2, G_FILE_TEST_IS_REGULAR)) {
        return g_strdup(cand2);
    }
    if (cand3 != NULL && g_file_test(cand3, G_FILE_TEST_IS_REGULAR)) {
        return g_strdup(cand3);
    }

    return g_strdup("python");
}

static void
append_log_level(AppState *app, LogLevel level, const char *format, ...)
{
    g_autoptr(GDateTime) now = g_date_time_new_now_local();
    g_autofree gchar *stamp = g_date_time_format(now, "%H:%M:%S");
    va_list args;
    g_autofree gchar *message = NULL;
    g_autofree gchar *line = NULL;
    GtkTextIter end_iter;

    va_start(args, format);
    message = g_strdup_vprintf(format, args);
    va_end(args);

    line = g_strdup_printf("%s [%s] %s\n", stamp, log_level_to_string(level), message);

    if (level >= app->log_level && app->log_buffer != NULL && GTK_IS_TEXT_BUFFER(app->log_buffer)) {
        gtk_text_buffer_get_end_iter(app->log_buffer, &end_iter);
        gtk_text_buffer_insert(app->log_buffer, &end_iter, stamp, -1);
        gtk_text_buffer_insert(app->log_buffer, &end_iter, " [", -1);
        gtk_text_buffer_insert(app->log_buffer, &end_iter, log_level_to_string(level), -1);
        gtk_text_buffer_insert(app->log_buffer, &end_iter, "] ", -1);
        gtk_text_buffer_insert(app->log_buffer, &end_iter, message, -1);
        gtk_text_buffer_insert(app->log_buffer, &end_iter, "\n", -1);
    }

    if (app->log_file_path != NULL && app->log_file_path[0] != '\0') {
        g_autofree gchar *log_dir = g_path_get_dirname(app->log_file_path);
        FILE *handle = NULL;
        g_mkdir_with_parents(log_dir, 0755);
        handle = fopen(app->log_file_path, "a");
        if (handle != NULL) {
            fputs(line, handle);
            fclose(handle);
        }
    }
}

static gboolean
service_is_running(ManagedService *service)
{
    gboolean alive = FALSE;

    if (service->process != NULL && service->tracked_pid <= 0) {
        const gchar *identifier = g_subprocess_get_identifier(service->process);
        if (identifier != NULL && identifier[0] != '\0') {
            gchar *end_ptr = NULL;
            long parsed_pid = strtol(identifier, &end_ptr, 10);
            if (end_ptr != identifier && parsed_pid > 0 && parsed_pid <= G_MAXINT) {
                service->tracked_pid = (gint) parsed_pid;
            }
        }
    }

    if (service->tracked_pid <= 0) {
        return service->process != NULL;
    }

#ifdef G_OS_WIN32
    HANDLE process_handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, (DWORD) service->tracked_pid);
    if (process_handle != NULL) {
        DWORD exit_code = 0;
        alive = GetExitCodeProcess(process_handle, &exit_code) && exit_code == STILL_ACTIVE;
        CloseHandle(process_handle);
    }
#else
    alive = (kill(service->tracked_pid, 0) == 0 || errno == EPERM);
#endif

    if (alive) {
        return TRUE;
    }

    if (service->process == NULL) {
        service->tracked_pid = 0;
        if (service->pid_file_path != NULL) {
            g_remove(service->pid_file_path);
        }
    }

    return FALSE;
}

static gboolean
service_has_pid_file(ManagedService *service)
{
    return service->pid_file_path != NULL && g_file_test(service->pid_file_path, G_FILE_TEST_IS_REGULAR);
}

static gchar *
build_service_pid_file_path(AppState *app, const gchar *service_key)
{
    const gchar *cache_home = g_getenv("XDG_CACHE_HOME");
    g_autofree gchar *cache_dir = NULL;
    g_autofree gchar *file_name = NULL;

    if (cache_home != NULL && cache_home[0] != '\0') {
        cache_dir = g_strdup(cache_home);
    } else {
        cache_dir = g_build_filename(app->project_root, "cache", NULL);
    }

    g_mkdir_with_parents(cache_dir, 0755);
    file_name = g_strdup_printf("%s.pid", service_key);
    return g_build_filename(cache_dir, file_name, NULL);
}

static void
clear_service_pid_tracking(ManagedService *service)
{
    service->tracked_pid = 0;
    if (service->pid_file_path != NULL) {
        g_remove(service->pid_file_path);
    }
}

static void
write_service_pid_file(AppState *app, ManagedService *service, gint pid)
{
    g_autofree gchar *text = NULL;

    if (pid <= 0 || service->pid_file_path == NULL) {
        return;
    }

    service->tracked_pid = pid;
    text = g_strdup_printf("%d\n", pid);
    if (!g_file_set_contents(service->pid_file_path, text, -1, NULL)) {
        append_log_level(app, LOG_LEVEL_WARN, "[%s] could not write pid file: %s", service->key, service->pid_file_path);
    }
}

static void
load_service_pid_file(AppState *app, ManagedService *service)
{
    g_autofree gchar *contents = NULL;
    gsize len = 0;
    gchar *end_ptr = NULL;
    long parsed_pid;

    if (service->pid_file_path == NULL || !g_file_test(service->pid_file_path, G_FILE_TEST_IS_REGULAR)) {
        return;
    }

    if (!g_file_get_contents(service->pid_file_path, &contents, &len, NULL) || contents == NULL) {
        return;
    }

    parsed_pid = strtol(contents, &end_ptr, 10);
    if (end_ptr == contents || parsed_pid <= 0 || parsed_pid > G_MAXINT) {
        clear_service_pid_tracking(service);
        return;
    }

    service->tracked_pid = (gint) parsed_pid;
    if (service_is_running(service)) {
        append_log_level(app, LOG_LEVEL_INFO, "[%s] recovered running pid %d from cache", service->key, service->tracked_pid);
    } else {
        clear_service_pid_tracking(service);
    }
}

static gboolean
terminate_external_pid(gint pid)
{
    if (pid <= 0) {
        return FALSE;
    }

#ifdef G_OS_WIN32
    HANDLE process_handle = OpenProcess(PROCESS_TERMINATE, FALSE, (DWORD) pid);
    if (process_handle == NULL) {
        return FALSE;
    }

    BOOL terminated = TerminateProcess(process_handle, 1);
    CloseHandle(process_handle);
    return terminated != 0;
#else
    return kill(pid, SIGTERM) == 0;
#endif
}

static gint
service_port_from_health_url(const gchar *health_url)
{
    g_autoptr(GError) error = NULL;
    g_autoptr(GUri) uri = g_uri_parse(health_url, G_URI_FLAGS_NONE, &error);
    if (uri == NULL) {
        return -1;
    }

    return g_uri_get_port(uri);
}

static gboolean
is_local_port_in_use(gint port)
{
    g_autoptr(GSocketClient) client = g_socket_client_new();
    g_autoptr(GError) error = NULL;
    g_autoptr(GSocketConnection) connection = NULL;

    if (port <= 0) {
        return FALSE;
    }

    g_socket_client_set_timeout(client, 1);
    connection = g_socket_client_connect_to_host(client, "127.0.0.1", port, NULL, &error);
    return connection != NULL;
}

static gboolean
recover_pid_from_port_if_matching(AppState *app, ManagedService *service)
{
    gint port;

    if (service->tracked_pid > 0 || service_has_pid_file(service)) {
        return FALSE;
    }

    port = service_port_from_health_url(service->health_url);
    if (port <= 0 || !is_local_port_in_use(port)) {
        return FALSE;
    }

#ifdef G_OS_WIN32
    g_autofree gchar *bash_path = g_find_program_in_path("bash");
    g_autofree gchar *pid_query = NULL;
    g_autofree gchar *pid_stdout = NULL;
    g_autofree gchar *pid_stderr = NULL;
    g_autoptr(GError) spawn_error = NULL;
    gint spawn_status = 0;
    gint discovered_pid = 0;

    if (bash_path == NULL || !g_file_test(bash_path, G_FILE_TEST_IS_REGULAR)) {
        return FALSE;
    }

    pid_query = g_strdup_printf(
        "netstat -ano -p tcp | tr -d '\\r' | grep -E ':%d[[:space:]]+[^[:space:]]+:0[[:space:]]' | head -n 1 | tr -s ' ' | cut -d ' ' -f 5",
        port
    );
    gchar *pid_argv[] = { bash_path, "-lc", pid_query, NULL };

    if (!g_spawn_sync(
            NULL,
            pid_argv,
            NULL,
            0,
            NULL,
            NULL,
            &pid_stdout,
            &pid_stderr,
            &spawn_status,
            &spawn_error
        )) {
        return FALSE;
    }

    if (pid_stdout != NULL) {
        g_strstrip(pid_stdout);
        if (pid_stdout[0] != '\0') {
            gchar *end_ptr = NULL;
            long parsed_pid = strtol(pid_stdout, &end_ptr, 10);
            if (end_ptr != pid_stdout && parsed_pid > 0 && parsed_pid <= G_MAXINT) {
                discovered_pid = (gint) parsed_pid;
            }
        }
    }

    if (discovered_pid <= 0) {
        return FALSE;
    }

    if (service->process_match_token == NULL || service->process_match_token[0] == '\0') {
        write_service_pid_file(app, service, discovered_pid);
        append_log_level(app, LOG_LEVEL_INFO, "[%s] discovered running pid %d from port %d", service->key, service->tracked_pid, port);
        return TRUE;
    }

    g_autofree gchar *script = g_strdup_printf(
        "ps -W -f | tr -s ' ' | grep -E '^[^ ]+ %d ' | head -n 1",
        discovered_pid
    );
    gchar *argv[] = { bash_path, "-lc", script, NULL };
    g_autofree gchar *stdout_str = NULL;
    g_autofree gchar *stderr_str = NULL;
    gint exit_status = 0;
    g_autoptr(GError) error = NULL;

    if (!g_spawn_sync(
            NULL,
            argv,
            NULL,
            G_SPAWN_SEARCH_PATH,
            NULL,
            NULL,
            &stdout_str,
            &stderr_str,
            &exit_status,
            &error
        )) {
        return FALSE;
    }

    if (stdout_str == NULL || stdout_str[0] == '\0') {
        return FALSE;
    }

    g_strstrip(stdout_str);
    if (stdout_str[0] != '\0' && g_strrstr(stdout_str, service->process_match_token) != NULL) {
        write_service_pid_file(app, service, discovered_pid);
        append_log_level(app, LOG_LEVEL_INFO, "[%s] discovered running pid %d from port %d", service->key, service->tracked_pid, port);
        return TRUE;
    }

    append_log_level(app, LOG_LEVEL_DEBUG, "[%s] port %d owned by pid %d but commandline did not match", service->key, port, discovered_pid);
    if (stderr_str != NULL && stderr_str[0] != '\0') {
        append_log_level(app, LOG_LEVEL_DEBUG, "[%s] pid probe stderr: %s", service->key, stderr_str);
    }
#endif

    return FALSE;
}

static gboolean
force_stop_timeout_cb(gpointer user_data)
{
    StopTimeoutContext *context = user_data;
    AppState *app = context->app;
    ManagedService *service = context->service;

    service->stop_timeout_source_id = 0;
    if (service_is_running(service)) {
        append_log_level(app, LOG_LEVEL_WARN, "[%s] graceful stop timed out, forcing exit", service->key);
        g_subprocess_force_exit(service->process);
    }

    return G_SOURCE_REMOVE;
}

static void
service_cleanup_process(ManagedService *service)
{
    if (service->stop_timeout_source_id != 0) {
        g_source_remove(service->stop_timeout_source_id);
        service->stop_timeout_source_id = 0;
    }
    g_clear_object(&service->log_stream);
    g_clear_object(&service->process);
    service->exit_logged = FALSE;
    service->health_fail_streak = 0;
    service->run_source = RUN_SOURCE_STOPPED;
}

static void schedule_log_read(AppState *app, ManagedService *service);

static void
on_log_line_read(GObject *source_object, GAsyncResult *result, gpointer user_data)
{
    LogReadContext *context = user_data;
    AppState *app = context->app;
    ManagedService *service = context->service;
    g_autoptr(GError) error = NULL;
    gsize length = 0;
    g_autofree gchar *line = g_data_input_stream_read_line_finish_utf8(
        G_DATA_INPUT_STREAM(source_object),
        result,
        &length,
        &error
    );

    if (app->is_shutting_down) {
        g_free(context);
        return;
    }

    if (error != NULL) {
        append_log_level(app, LOG_LEVEL_ERROR, "[%s] log stream error: %s", service->key, error->message);
        g_free(context);
        return;
    }

    if (line == NULL) {
        g_free(context);
        return;
    }

    if (length > 0) {
        append_log_level(app, LOG_LEVEL_INFO, "[%s] %s", service->key, line);
    }

    schedule_log_read(app, service);
    g_free(context);
}

static void
schedule_log_read(AppState *app, ManagedService *service)
{
    LogReadContext *context;

    if (app->is_shutting_down) {
        return;
    }

    if (service->log_stream == NULL) {
        return;
    }

    context = g_new0(LogReadContext, 1);
    context->app = app;
    context->service = service;

    g_data_input_stream_read_line_async(
        service->log_stream,
        G_PRIORITY_DEFAULT,
        NULL,
        on_log_line_read,
        context
    );
}

static gboolean
status_is_healthy(const gchar *status)
{
    if (status == NULL || status[0] == '\0') {
        return TRUE;
    }

    return g_ascii_strcasecmp(status, "up") == 0 ||
           g_ascii_strcasecmp(status, "ok") == 0 ||
           g_ascii_strcasecmp(status, "healthy") == 0 ||
            g_ascii_strcasecmp(status, "ready") == 0 ||
            g_ascii_strcasecmp(status, "success") == 0;
}

static void
update_health_label(ManagedService *service, gboolean healthy, const gchar *detail)
{
    gboolean running = service_is_running(service);
    const gchar *display = (detail != NULL && detail[0] != '\0') ? detail : "down";

    if (healthy) {
        g_autofree gchar *text = g_markup_printf_escaped(
            "<span foreground=\"#0f766e\" weight=\"700\">HEALTH  %s</span>",
            display
        );
        gtk_label_set_markup(service->health_label, text);
    } else if (running) {
        g_autofree gchar *text = g_markup_printf_escaped(
            "<span foreground=\"#d97706\" weight=\"700\">HEALTH  %s</span>",
            display
        );
        gtk_label_set_markup(service->health_label, text);
    } else {
        g_autofree gchar *text = g_markup_printf_escaped(
            "<span foreground=\"#b91c1c\" weight=\"700\">HEALTH  %s</span>",
            display
        );
        gtk_label_set_markup(service->health_label, text);
    }
}

static void
apply_health_result(AppState *app, ManagedService *service, gboolean healthy, const gchar *detail)
{
    if (app->is_shutting_down) {
        return;
    }

    if (healthy) {
        service->health_fail_streak = 0;
        update_health_label(service, TRUE, detail);
        return;
    }

    if (!service_is_running(service)) {
        service->health_fail_streak = 0;
        update_health_label(service, FALSE, detail);
        return;
    }

    service->health_fail_streak++;
    if (service->health_fail_streak < HEALTH_FAIL_DEBOUNCE_THRESHOLD) {
        append_log_level(
            app,
            LOG_LEVEL_DEBUG,
            "[%s] health transient (%u/%u): %s",
            service->key,
            service->health_fail_streak,
            HEALTH_FAIL_DEBOUNCE_THRESHOLD,
            detail != NULL ? detail : "down"
        );
        return;
    }

    update_health_label(service, FALSE, detail);
}

static void
on_health_probe_done(GObject *source_object, GAsyncResult *result, gpointer user_data)
{
    HealthProbeContext *ctx = user_data;
    AppState *app = ctx->app;
    ManagedService *service = ctx->service;
    g_autoptr(SoupMessage) message = ctx->message;
    g_free(ctx);

    g_autoptr(GError) error = NULL;
    g_autoptr(GBytes) bytes = soup_session_send_and_read_finish(
        SOUP_SESSION(source_object), result, &error);

    if (app->is_shutting_down) {
        return;
    }

    if (error != NULL) {
        if (g_error_matches(error, G_IO_ERROR, G_IO_ERROR_CANCELLED)) {
            return;
        }
        const gchar *detail = g_error_matches(error, G_IO_ERROR, G_IO_ERROR_TIMED_OUT)
            ? "timeout" : "down";
        append_log_level(app, LOG_LEVEL_DEBUG, "[%s] health probe: %s", service->key, error->message);
        apply_health_result(app, service, FALSE, detail);
        return;
    }

    guint status_code = soup_message_get_status(message);
    if (status_code < 200 || status_code >= 300) {
        g_autofree gchar *detail = g_strdup_printf("http %u", status_code);
        apply_health_result(app, service, FALSE, detail);
        return;
    }

    gsize body_size = 0;
    const gchar *body = bytes != NULL ? g_bytes_get_data(bytes, &body_size) : NULL;
    if (body == NULL || body_size == 0) {
        apply_health_result(app, service, TRUE, "up");
        return;
    }

    g_autoptr(JsonParser) parser = json_parser_new();
    if (json_parser_load_from_data(parser, body, (gssize) body_size, NULL)) {
        JsonNode *root = json_parser_get_root(parser);
        if (root != NULL && JSON_NODE_HOLDS_OBJECT(root)) {
            JsonObject *object = json_node_get_object(root);
            if (json_object_has_member(object, "status")) {
                JsonNode *status_node = json_object_get_member(object, "status");

                if (status_node != NULL && JSON_NODE_HOLDS_VALUE(status_node)) {
                    const gchar *status = json_node_get_string(status_node);
                    apply_health_result(app, service, status_is_healthy(status),
                                        status != NULL ? status : "up");
                    return;
                }

                if (status_node != NULL && JSON_NODE_HOLDS_OBJECT(status_node)) {
                    JsonObject *status_object = json_node_get_object(status_node);
                    const gchar *nested_status = NULL;

                    if (json_object_has_member(status_object, "status")) {
                        JsonNode *nested_node = json_object_get_member(status_object, "status");
                        if (nested_node != NULL && JSON_NODE_HOLDS_VALUE(nested_node)) {
                            nested_status = json_node_get_string(nested_node);
                        }
                    }

                    apply_health_result(app, service, status_is_healthy(nested_status),
                                        nested_status != NULL ? nested_status : "up");
                    return;
                }
            }
        }
    }

    apply_health_result(app, service, TRUE, "up");
}

static void
fire_health_probe_async(AppState *app, ManagedService *service)
{
    if (app->is_shutting_down) {
        return;
    }

    HealthProbeContext *ctx = g_new0(HealthProbeContext, 1);
    ctx->app = app;
    ctx->service = service;
    ctx->message = soup_message_new("GET", service->health_url);

    soup_session_send_and_read_async(
        app->http_session,
        ctx->message,
        G_PRIORITY_DEFAULT,
        NULL,
        on_health_probe_done,
        ctx
    );
}

static void
refresh_service_row(AppState *app, ManagedService *service)
{
    gboolean running = FALSE;
    gboolean restart_after_exit = FALSE;
    gint exit_code = -1;
    gboolean has_exit_code = FALSE;
    RunSource source = RUN_SOURCE_STOPPED;

    running = service_is_running(service);
    if (running) {
        source = service->process != NULL ? RUN_SOURCE_OWNED_SUBPROCESS : RUN_SOURCE_CACHED_PID;
    }

    if (source != service->run_source) {
        const gchar *source_text = "stopped";
        if (source == RUN_SOURCE_OWNED_SUBPROCESS) {
            source_text = "owned-subprocess";
        } else if (source == RUN_SOURCE_CACHED_PID) {
            source_text = "cached-pid";
        }
        append_log_level(app, LOG_LEVEL_INFO, "[%s] run source -> %s", service->key, source_text);
        service->run_source = source;
    }

    if (!running && service->process != NULL) {
        restart_after_exit = service->restart_pending;
        service->restart_pending = FALSE;
        if (service->process != NULL) {
            exit_code = g_subprocess_get_exit_status(service->process);
            has_exit_code = TRUE;
        }
        if (!service->exit_logged) {
            append_log_level(app, exit_code == 0 ? LOG_LEVEL_INFO : LOG_LEVEL_WARN, "[%s] process exited (code %d)", service->key, exit_code);
            service->exit_logged = TRUE;
        }
        clear_service_pid_tracking(service);
        service_cleanup_process(service);
        if (restart_after_exit) {
            gboolean do_restart = !has_exit_code || exit_code == 0 || app->restart_on_nonzero;
            if (do_restart) {
                const gchar *reason = (has_exit_code && exit_code != 0)
                    ? "non-zero exit (restart_on_nonzero=true)" : "clean stop";
                append_log_level(app, LOG_LEVEL_INFO, "[%s] restart after %s (code %d)",
                                  service->key, reason, has_exit_code ? exit_code : 0);
                service_start(app, service);
                running = service_is_running(service);
            } else {
                append_log_level(
                    app,
                    LOG_LEVEL_ERROR,
                    "[%s] restart canceled due to non-zero exit (code %d)",
                    service->key,
                    has_exit_code ? exit_code : -1
                );
            }
        }
    }

    if (running) {
        g_autofree gchar *pid_markup = NULL;
        gtk_label_set_markup(service->process_label, "<span foreground=\"#047857\" weight=\"700\">PROCESS  RUNNING</span>");
        if (service->process != NULL) {
            const gchar *identifier = g_subprocess_get_identifier(service->process);
            pid_markup = g_markup_printf_escaped(
                "<span foreground=\"#334155\" weight=\"700\">PID  %s</span>",
                identifier != NULL ? identifier : "?"
            );
        } else if (service->tracked_pid > 0) {
            pid_markup = g_markup_printf_escaped(
                "<span foreground=\"#334155\" weight=\"700\">PID  %d (cached)</span>",
                service->tracked_pid
            );
        } else {
            pid_markup = g_markup_printf_escaped(
                "<span foreground=\"#334155\" weight=\"700\">PID  ?</span>"
            );
        }
        gtk_label_set_markup(service->pid_label, pid_markup);
    } else {
        gtk_label_set_markup(service->process_label, "<span foreground=\"#b91c1c\" weight=\"700\">PROCESS  STOPPED</span>");
        gtk_label_set_markup(service->pid_label, "<span foreground=\"#334155\" weight=\"700\">PID  -</span>");
    }

    fire_health_probe_async(app, service);
}

static gboolean
poll_services(gpointer user_data)
{
    AppState *app = user_data;

    for (int i = 0; i < SERVICE_COUNT; i++) {
        refresh_service_row(app, &app->services[i]);
    }

    return G_SOURCE_CONTINUE;
}

static gboolean
poll_services_once(gpointer user_data)
{
    AppState *app = user_data;
    if (app != NULL) {
        app->poll_once_source_id = 0;
        for (int i = 0; i < SERVICE_COUNT; i++) {
            recover_pid_from_port_if_matching(app, &app->services[i]);
        }
    }
    poll_services(user_data);
    return G_SOURCE_REMOVE;
}

static void
service_start(AppState *app, ManagedService *service)
{
    g_autoptr(GSubprocessLauncher) launcher = g_subprocess_launcher_new(
        G_SUBPROCESS_FLAGS_STDOUT_PIPE | G_SUBPROCESS_FLAGS_STDERR_MERGE
    );
    g_autoptr(GError) error = NULL;

    recover_pid_from_port_if_matching(app, service);

    if (service_is_running(service)) {
        if (service->process == NULL && service->tracked_pid > 0) {
            append_log_level(app, LOG_LEVEL_WARN, "[%s] already running (cached pid %d)", service->key, service->tracked_pid);
        } else {
            append_log_level(app, LOG_LEVEL_WARN, "[%s] already running", service->key);
        }
        return;
    }

    gint target_port = service_port_from_health_url(service->health_url);
    if (target_port > 0 && is_local_port_in_use(target_port)) {
        append_log_level(app, LOG_LEVEL_ERROR, "[%s] start skipped: port %d already in use", service->key, target_port);
        return;
    }

    service_cleanup_process(service);
    g_subprocess_launcher_set_cwd(launcher, service->cwd);
    g_subprocess_launcher_setenv(launcher, "LIARA_PROJECT_ROOT", app->project_root, TRUE);
    g_subprocess_launcher_setenv(launcher, "PYTHONUNBUFFERED", "1", TRUE);
    apply_env_file_to_launcher(app, launcher);
    service->process = g_subprocess_launcher_spawnv(
        launcher,
        (const gchar * const *) service->argv,
        &error
    );

    if (error != NULL || service->process == NULL) {
        append_log_level(app, LOG_LEVEL_ERROR, "[%s] start failed: %s", service->key, error != NULL ? error->message : "unknown");
        return;
    }

    GInputStream *stdout_pipe = g_subprocess_get_stdout_pipe(service->process);
    service->log_stream = g_data_input_stream_new(stdout_pipe);
    g_data_input_stream_set_newline_type(service->log_stream, G_DATA_STREAM_NEWLINE_TYPE_ANY);
    schedule_log_read(app, service);

    append_log_level(app, LOG_LEVEL_INFO, "[%s] started", service->key);
    append_log_level(app, LOG_LEVEL_DEBUG, "[%s] cwd=%s", service->key, service->cwd);
    append_log_level(app, LOG_LEVEL_DEBUG, "[%s] env=%s", service->key, app->env_file_path != NULL ? app->env_file_path : "(none)");

    const gchar *identifier = g_subprocess_get_identifier(service->process);
    if (identifier != NULL && identifier[0] != '\0') {
        gchar *end_ptr = NULL;
        long parsed_pid = strtol(identifier, &end_ptr, 10);
        if (end_ptr != identifier && parsed_pid > 0 && parsed_pid <= G_MAXINT) {
            write_service_pid_file(app, service, (gint) parsed_pid);
        }
    }
}

static void
service_stop(AppState *app, ManagedService *service)
{
    service->restart_pending = FALSE;
    service->health_fail_streak = 0;

    recover_pid_from_port_if_matching(app, service);

    if (!service_is_running(service)) {
        append_log_level(app, LOG_LEVEL_WARN, "[%s] not running", service->key);
        return;
    }

    if (service->process == NULL && service->tracked_pid > 0) {
        gint external_pid = service->tracked_pid;
        if (terminate_external_pid(external_pid)) {
            append_log_level(app, LOG_LEVEL_INFO, "[%s] stopped external pid %d", service->key, external_pid);
        } else {
            append_log_level(app, LOG_LEVEL_WARN, "[%s] failed to stop external pid %d", service->key, external_pid);
        }
        clear_service_pid_tracking(service);
        return;
    }

#ifdef G_OS_UNIX
    g_subprocess_send_signal(service->process, SIGTERM);
    append_log_level(app, LOG_LEVEL_INFO, "[%s] graceful stop requested (SIGTERM)", service->key);
#else
    append_log_level(app, LOG_LEVEL_INFO, "[%s] graceful stop requested (force-exit after grace period)", service->key);
#endif

    if (service->stop_timeout_source_id == 0) {
        StopTimeoutContext *context = g_new0(StopTimeoutContext, 1);
        context->app = app;
        context->service = service;
        service->stop_timeout_source_id = g_timeout_add_full(
            G_PRIORITY_DEFAULT,
            STOP_GRACE_MILLISECONDS,
            force_stop_timeout_cb,
            context,
            g_free
        );
    }
}

static void
service_restart(AppState *app, ManagedService *service)
{
    if (service_is_running(service)) {
        append_log_level(app, LOG_LEVEL_INFO, "[%s] restart requested", service->key);
        service_stop(app, service);
        service->restart_pending = TRUE;
        return;
    }

    service->restart_pending = FALSE;
    service_start(app, service);
}

static void
on_start_clicked(GtkButton *button, gpointer user_data)
{
    AppState *app = user_data;
    int index = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(button), "svc-index"));
    service_start(app, &app->services[index]);
}

static void
on_stop_clicked(GtkButton *button, gpointer user_data)
{
    AppState *app = user_data;
    int index = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(button), "svc-index"));
    service_stop(app, &app->services[index]);
}

static void
on_restart_clicked(GtkButton *button, gpointer user_data)
{
    AppState *app = user_data;
    int index = GPOINTER_TO_INT(g_object_get_data(G_OBJECT(button), "svc-index"));
    service_restart(app, &app->services[index]);
}

static void
on_start_all_clicked(GtkButton *button, gpointer user_data)
{
    AppState *app = user_data;
    (void) button;
    start_all_sequential(app);
}

static gboolean
sequential_start_next_cb(gpointer user_data)
{
    SequentialStartContext *ctx = user_data;
    if (ctx->app->is_shutting_down || ctx->next_index >= SERVICE_COUNT) {
        ctx->app->sequential_start_source_id = 0;
        return G_SOURCE_REMOVE;
    }
    service_start(ctx->app, &ctx->app->services[ctx->next_index]);
    ctx->next_index++;
    if (ctx->next_index < SERVICE_COUNT) {
        return G_SOURCE_CONTINUE;
    }

    ctx->app->sequential_start_source_id = 0;
    return G_SOURCE_REMOVE;
}

static void
start_all_sequential(AppState *app)
{
    if (SERVICE_COUNT == 0)
        return;
    service_start(app, &app->services[0]);
    if (SERVICE_COUNT <= 1 || app->start_delay_ms == 0) {
        for (int i = 1; i < SERVICE_COUNT; i++)
            service_start(app, &app->services[i]);
        return;
    }
    SequentialStartContext *ctx = g_new0(SequentialStartContext, 1);
    ctx->app = app;
    ctx->next_index = 1;
    if (app->sequential_start_source_id != 0) {
        g_source_remove(app->sequential_start_source_id);
        app->sequential_start_source_id = 0;
    }
    app->sequential_start_source_id = g_timeout_add_full(
        G_PRIORITY_DEFAULT,
        app->start_delay_ms,
        sequential_start_next_cb,
        ctx,
        g_free
    );
}

static void
on_stop_all_clicked(GtkButton *button, gpointer user_data)
{
    AppState *app = user_data;
    (void) button;
    for (int i = 0; i < SERVICE_COUNT; i++) {
        service_stop(app, &app->services[i]);
    }
}

static void
on_restart_all_clicked(GtkButton *button, gpointer user_data)
{
    AppState *app = user_data;
    (void) button;
    for (int i = 0; i < SERVICE_COUNT; i++) {
        service_restart(app, &app->services[i]);
    }
}

static GtkWidget *
build_service_row(AppState *app, ManagedService *service, int index)
{
    GtkWidget *row = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    GtkWidget *top = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);
    GtkWidget *meta = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    GtkWidget *badges = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *name = gtk_label_new(service->name);
    GtkWidget *url = gtk_label_new(service->health_url);
    GtkWidget *proc = gtk_label_new(NULL);
    GtkWidget *pid = gtk_label_new(NULL);
    GtkWidget *health = gtk_label_new(NULL);
    GtkWidget *start_btn = gtk_button_new_with_label("Start");
    GtkWidget *stop_btn = gtk_button_new_with_label("Stop");
    GtkWidget *restart_btn = gtk_button_new_with_label("Restart");

    gtk_widget_add_css_class(row, "sm-card");
    gtk_widget_add_css_class(name, "sm-service-name");
    gtk_widget_add_css_class(url, "sm-service-url");
    gtk_widget_add_css_class(proc, "sm-badge");
    gtk_widget_add_css_class(pid, "sm-badge");
    gtk_widget_add_css_class(health, "sm-badge");
    gtk_widget_add_css_class(start_btn, "sm-primary");

    gtk_label_set_xalign(GTK_LABEL(name), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(url), 0.0f);
    gtk_widget_set_hexpand(meta, TRUE);
    gtk_widget_set_hexpand(top, TRUE);
    gtk_widget_set_halign(actions, GTK_ALIGN_END);
    gtk_box_append(GTK_BOX(meta), name);
    gtk_box_append(GTK_BOX(meta), url);
    gtk_box_append(GTK_BOX(top), meta);
    gtk_box_append(GTK_BOX(actions), start_btn);
    gtk_box_append(GTK_BOX(actions), stop_btn);
    gtk_box_append(GTK_BOX(actions), restart_btn);
    gtk_box_append(GTK_BOX(top), actions);
    gtk_box_append(GTK_BOX(badges), proc);
    gtk_box_append(GTK_BOX(badges), pid);
    gtk_box_append(GTK_BOX(badges), health);

    g_object_set_data(G_OBJECT(start_btn), "svc-index", GINT_TO_POINTER(index));
    g_object_set_data(G_OBJECT(stop_btn), "svc-index", GINT_TO_POINTER(index));
    g_object_set_data(G_OBJECT(restart_btn), "svc-index", GINT_TO_POINTER(index));
    g_signal_connect(start_btn, "clicked", G_CALLBACK(on_start_clicked), app);
    g_signal_connect(stop_btn, "clicked", G_CALLBACK(on_stop_clicked), app);
    g_signal_connect(restart_btn, "clicked", G_CALLBACK(on_restart_clicked), app);

    gtk_box_append(GTK_BOX(row), top);
    gtk_box_append(GTK_BOX(row), badges);

    service->process_label = GTK_LABEL(proc);
    service->pid_label = GTK_LABEL(pid);
    service->health_label = GTK_LABEL(health);

    refresh_service_row(app, service);

    return row;
}

static void
free_service_config(ManagedService *service)
{
    service_cleanup_process(service);
    g_clear_pointer(&service->argv, g_strfreev);
    g_clear_pointer(&service->cwd, g_free);
    g_clear_pointer(&service->pid_file_path, g_free);
}

static void
free_app_state(gpointer data)
{
    AppState *app = data;
    if (app == NULL) {
        return;
    }

    for (int i = 0; i < SERVICE_COUNT; i++) {
        free_service_config(&app->services[i]);
    }

    g_clear_object(&app->http_session);
    g_clear_pointer(&app->project_root, g_free);
    g_clear_pointer(&app->python_exe, g_free);
    g_clear_pointer(&app->config_path, g_free);
    g_clear_pointer(&app->env_file_path, g_free);
    g_clear_pointer(&app->log_file_path, g_free);
    g_free(app);
}

static void
on_window_destroy(GtkWindow *window, gpointer user_data)
{
    AppState *app = user_data;
    (void) window;

    append_log_level(app, LOG_LEVEL_INFO, "[system] shutdown begin");
    app->is_shutting_down = TRUE;

    if (app->present_source_id != 0) {
        g_source_remove(app->present_source_id);
        app->present_source_id = 0;
    }

    if (app->poll_once_source_id != 0) {
        g_source_remove(app->poll_once_source_id);
        app->poll_once_source_id = 0;
    }

    if (app->poll_source_id != 0) {
        g_source_remove(app->poll_source_id);
        app->poll_source_id = 0;
    }

    if (app->sequential_start_source_id != 0) {
        g_source_remove(app->sequential_start_source_id);
        app->sequential_start_source_id = 0;
    }

    if (app->http_session != NULL) {
        soup_session_abort(app->http_session);
    }

    for (int i = 0; i < SERVICE_COUNT; i++) {
        if (app->services[i].process != NULL) {
            g_subprocess_force_exit(app->services[i].process);
        }
        clear_service_pid_tracking(&app->services[i]);
        free_service_config(&app->services[i]);
    }

    append_log_level(app, LOG_LEVEL_INFO, "[system] shutdown complete");
    g_clear_object(&app->http_session);
}

static gchar **
build_service_argv(
    const gchar *python,
    const gchar *kind,
    const gchar *host_env,
    const gchar *host_default,
    const gchar *port_env,
    const gchar *port_default)
{
    const gchar *host = g_getenv(host_env);
    const gchar *port = g_getenv(port_env);

    if (host == NULL || host[0] == '\0') {
        host = host_default;
    }
    if (port == NULL || port[0] == '\0') {
        port = port_default;
    }

    gchar **argv = g_new0(gchar *, 9);
    argv[0] = g_strdup(python);
    argv[1] = g_strdup("-m");
    argv[2] = g_strdup("uvicorn");
    argv[3] = g_strdup(kind);
    argv[4] = g_strdup("--host");
    argv[5] = g_strdup(host);
    argv[6] = g_strdup("--port");
    argv[7] = g_strdup(port);
    return argv;
}

static void
initialize_services(AppState *app)
{
    ManagedService *api = &app->services[0];
    ManagedService *memory = &app->services[1];
    ManagedService *embedding = &app->services[2];

    api->key = "api";
    api->name = "LIARA API";
    api->health_url = "http://127.0.0.1:8010/health";
    api->process_match_token = "services.api.app";
    api->cwd = g_strdup(app->project_root);
    api->argv = g_new0(gchar *, 5);
    api->argv[0] = g_strdup(app->python_exe);
    api->argv[1] = g_strdup("-m");
    api->argv[2] = g_strdup("services.api.app");

    memory->key = "memory";
    memory->name = "LIARA Memory";
    memory->health_url = "http://127.0.0.1:8020/health";
    memory->process_match_token = "services.memory.app:app";
    memory->cwd = g_strdup(app->project_root);
    memory->argv = build_service_argv(
        app->python_exe,
        "services.memory.app:app",
        "LIARA_MEMORY_BIND_HOST",
        "0.0.0.0",
        "LIARA_MEMORY_PORT",
        "8020"
    );

    embedding->key = "embedding";
    embedding->name = "LIARA Embedding";
    embedding->health_url = "http://127.0.0.1:8030/health";
    embedding->process_match_token = "services.embedding.app:app";
    embedding->cwd = g_strdup(app->project_root);
    embedding->argv = build_service_argv(
        app->python_exe,
        "services.embedding.app:app",
        "LIARA_EMBEDDING_BIND_HOST",
        "0.0.0.0",
        "LIARA_EMBEDDING_PORT",
        "8030"
    );

    api->pid_file_path = build_service_pid_file_path(app, api->key);
    memory->pid_file_path = build_service_pid_file_path(app, memory->key);
    embedding->pid_file_path = build_service_pid_file_path(app, embedding->key);

    load_service_pid_file(app, api);
    load_service_pid_file(app, memory);
    load_service_pid_file(app, embedding);
}

static void
on_activate(GtkApplication *application, gpointer user_data)
{
    AppState *app = g_new0(AppState, 1);
    GtkWidget *root;
    GtkWidget *header;
    GtkWidget *title;
    GtkWidget *subtitle;
    GtkWidget *actions;
    GtkWidget *services_box;
    GtkWidget *services_card;
    GtkWidget *services_eyebrow;
    GtkWidget *scroll;
    GtkWidget *log_card;
    GtkWidget *log_eyebrow;
    GtkWidget *log_view;
    GtkWidget *start_all_btn;
    GtkWidget *stop_all_btn;
    GtkWidget *restart_all_btn;

    (void) user_data;
    app->application = application;
    g_object_set_data_full(G_OBJECT(application), "liara-app-state", app, free_app_state);
    app->project_root = find_project_root();
    app->python_exe = resolve_python_executable(app->project_root);
    app->log_file_path = build_default_log_file_path();
    ensure_log_file_exists(app);
    app->http_session = soup_session_new_with_options(
        "timeout", 2,
        "idle-timeout", 2,
        NULL
    );
    load_server_manager_config(app);
    initialize_services(app);
    load_server_manager_css();
    g_message("LIARA Server Manager project root: %s", app->project_root);

    app->window = GTK_WINDOW(gtk_application_window_new(application));
    gtk_window_set_title(app->window, "LIARA Server Manager");
    gtk_window_set_default_size(app->window, 1180, 760);
    gtk_widget_add_css_class(GTK_WIDGET(app->window), "server-manager-window");

    root = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    gtk_widget_add_css_class(root, "sm-shell");
    gtk_widget_set_margin_top(root, 12);
    gtk_widget_set_margin_bottom(root, 12);
    gtk_widget_set_margin_start(root, 12);
    gtk_widget_set_margin_end(root, 12);

    header = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);
    gtk_widget_add_css_class(header, "sm-card");
    title = gtk_label_new("LIARA Server Management");
    subtitle = gtk_label_new("Native service control for API, memory, embedding, health, and live logs");
    actions = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    services_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    log_card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 10);
    start_all_btn = gtk_button_new_with_label("Start All");
    stop_all_btn = gtk_button_new_with_label("Stop All");
    restart_all_btn = gtk_button_new_with_label("Restart All");
    services_eyebrow = gtk_label_new("SERVICES");
    log_eyebrow = gtk_label_new("LIVE LOG");

    gtk_label_set_xalign(GTK_LABEL(title), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(subtitle), 0.0f);
    gtk_widget_set_hexpand(title, TRUE);
    gtk_widget_add_css_class(title, "sm-title");
    gtk_widget_add_css_class(subtitle, "sm-subtitle");
    gtk_widget_add_css_class(services_card, "sm-card");
    gtk_widget_add_css_class(log_card, "sm-card");
    gtk_widget_add_css_class(start_all_btn, "sm-primary");
    gtk_widget_add_css_class(services_eyebrow, "sm-eyebrow");
    gtk_widget_add_css_class(log_eyebrow, "sm-eyebrow");
    gtk_label_set_xalign(GTK_LABEL(services_eyebrow), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(log_eyebrow), 0.0f);

    g_signal_connect(start_all_btn, "clicked", G_CALLBACK(on_start_all_clicked), app);
    g_signal_connect(stop_all_btn, "clicked", G_CALLBACK(on_stop_all_clicked), app);
    g_signal_connect(restart_all_btn, "clicked", G_CALLBACK(on_restart_all_clicked), app);

    gtk_box_append(GTK_BOX(actions), start_all_btn);
    gtk_box_append(GTK_BOX(actions), stop_all_btn);
    gtk_box_append(GTK_BOX(actions), restart_all_btn);

    GtkWidget *title_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    gtk_box_append(GTK_BOX(title_box), title);
    gtk_box_append(GTK_BOX(title_box), subtitle);
    gtk_box_append(GTK_BOX(header), title_box);
    gtk_box_append(GTK_BOX(header), actions);

    services_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    for (int i = 0; i < SERVICE_COUNT; i++) {
        GtkWidget *row = build_service_row(app, &app->services[i], i);
        gtk_box_append(GTK_BOX(services_box), row);
    }

    scroll = gtk_scrolled_window_new();
    gtk_widget_set_vexpand(scroll, TRUE);
    gtk_widget_add_css_class(scroll, "sm-log-view");
    log_view = gtk_text_view_new();
    gtk_text_view_set_editable(GTK_TEXT_VIEW(log_view), FALSE);
    gtk_text_view_set_cursor_visible(GTK_TEXT_VIEW(log_view), FALSE);
    gtk_text_view_set_wrap_mode(GTK_TEXT_VIEW(log_view), GTK_WRAP_WORD_CHAR);
    app->log_buffer = gtk_text_view_get_buffer(GTK_TEXT_VIEW(log_view));
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(scroll), log_view);

    gtk_box_append(GTK_BOX(root), header);
    gtk_box_append(GTK_BOX(services_card), services_eyebrow);
    gtk_box_append(GTK_BOX(services_card), services_box);
    gtk_box_append(GTK_BOX(root), services_card);
    gtk_box_append(GTK_BOX(log_card), log_eyebrow);
    gtk_box_append(GTK_BOX(log_card), scroll);
    gtk_box_append(GTK_BOX(root), log_card);

    gtk_window_set_child(app->window, root);
    g_signal_connect(app->window, "destroy", G_CALLBACK(on_window_destroy), app);
    append_log_level(app, LOG_LEVEL_INFO, "Project root: %s", app->project_root);
    append_log_level(app, LOG_LEVEL_INFO, "Python: %s", app->python_exe);
    append_log_level(app, LOG_LEVEL_INFO, "Config: %s", app->config_path != NULL ? app->config_path : "(none)");
    append_log_level(app, LOG_LEVEL_INFO, "Env file: %s", app->env_file_path != NULL ? app->env_file_path : "(none)");
    append_log_level(app, LOG_LEVEL_INFO, "Log file: %s", app->log_file_path != NULL ? app->log_file_path : "(none)");
    append_log_level(app, LOG_LEVEL_INFO, "Autostart: %s", app->autostart_enabled ? "on" : "off");
    append_log_level(app, LOG_LEVEL_INFO, "Log level: %s", log_level_to_string(app->log_level));
    append_log_level(app, LOG_LEVEL_INFO, "Server Manager version: %s", LIARA_SERVER_MANAGER_VERSION);

    append_log_level(app, LOG_LEVEL_DEBUG, "[system] project root: %s", app->project_root);
    append_log_level(app, LOG_LEVEL_DEBUG, "[system] python: %s", app->python_exe);
    append_log_level(app, LOG_LEVEL_INFO, "[system] C GUI ready");

    gtk_widget_set_visible(GTK_WIDGET(app->window), TRUE);
    gtk_window_present(app->window);
    app->present_attempts = 0;
    app->present_source_id = g_timeout_add(300, ensure_window_foreground, app);
    app->poll_once_source_id = g_timeout_add(150, poll_services_once, app);
    app->poll_source_id = g_timeout_add_seconds(POLL_INTERVAL_SECONDS, poll_services, app);
    if (app->autostart_enabled) {
        append_log_level(app, LOG_LEVEL_INFO, "[system] autostart enabled, starting services sequentially (delay %ums)", app->start_delay_ms);
        start_all_sequential(app);
    }
}

int
main(int argc, char **argv)
{
    GtkApplication *application;
    int status;

    (void) argv;
    application = gtk_application_new(
        "ai.liara.server-manager",
        G_APPLICATION_DEFAULT_FLAGS | G_APPLICATION_NON_UNIQUE
    );
    g_signal_connect(application, "activate", G_CALLBACK(on_activate), NULL);
    status = g_application_run(G_APPLICATION(application), argc, argv);
    g_object_unref(application);
    return status;
}
