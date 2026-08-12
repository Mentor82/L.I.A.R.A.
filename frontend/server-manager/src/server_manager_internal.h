#ifndef LIARA_SERVER_MANAGER_INTERNAL_H
#define LIARA_SERVER_MANAGER_INTERNAL_H

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
    GtkLabel *status_dot;
    gboolean  last_health_ok;

    GtkTextBuffer *service_log_buffer;
    GtkTextView   *service_log_view;
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
    GtkLabel    *status_summary_label;
    GtkNotebook *log_notebook;
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

const gchar *log_level_to_string(LogLevel level);
LogLevel parse_log_level(const gchar *value);
void append_log_level(AppState *app, LogLevel level, const char *format, ...);
void service_log_append(ManagedService *service, const gchar *line);

gboolean ensure_window_foreground(gpointer user_data);
void load_server_manager_css(void);
void on_start_all_clicked(GtkButton *button, gpointer user_data);
void on_stop_all_clicked(GtkButton *button, gpointer user_data);
void update_status_dot(AppState *app, ManagedService *service);
void update_status_summary(AppState *app);
GtkWidget *build_sidebar(AppState *app);
GtkWidget *build_log_notebook(AppState *app);
GtkWidget *build_service_row(AppState *app, ManagedService *service, int index);

gchar *build_default_env_file_path(const gchar *project_root);
gchar *build_default_log_file_path(void);
void ensure_log_file_exists(AppState *app);
gchar *build_server_manager_config_path(void);
void save_server_manager_config(AppState *app);
void load_server_manager_config(AppState *app);
gchar *trimmed_copy(const gchar *value);
gchar *normalize_env_value(const gchar *raw_value);
void apply_env_file_to_launcher(AppState *app, GSubprocessLauncher *launcher);

gchar *find_project_root(void);
gchar *resolve_python_executable(const gchar *project_root);
gchar **build_service_argv(
    const gchar *python,
    const gchar *kind,
    const gchar *host_env,
    const gchar *host_default,
    const gchar *port_env,
    const gchar *port_default);
void initialize_services(AppState *app);
void on_window_destroy(GtkWindow *window, gpointer user_data);
void on_activate(GtkApplication *application, gpointer user_data);

gboolean service_is_running(ManagedService *service);
gchar *build_service_pid_file_path(AppState *app, const gchar *service_key);
void clear_service_pid_tracking(ManagedService *service);
void write_service_pid_file(AppState *app, ManagedService *service, gint pid);
void load_service_pid_file(AppState *app, ManagedService *service);
gboolean terminate_external_pid(gint pid);
gint service_port_from_health_url(const gchar *health_url);
gboolean is_local_port_in_use(gint port);
gboolean recover_pid_from_port_if_matching(AppState *app, ManagedService *service);
gboolean force_stop_timeout_cb(gpointer user_data);
void service_cleanup_process(ManagedService *service);
void schedule_log_read(AppState *app, ManagedService *service);
void service_start(AppState *app, ManagedService *service);
void service_stop(AppState *app, ManagedService *service);
void service_restart(AppState *app, ManagedService *service);
void start_all_sequential(AppState *app);

gboolean status_is_healthy(const gchar *status);
void update_health_label(ManagedService *service, gboolean healthy, const gchar *detail);
void apply_health_result(AppState *app, ManagedService *service, gboolean healthy, const gchar *detail);
void on_health_probe_done(GObject *source_object, GAsyncResult *result, gpointer user_data);
void fire_health_probe_async(AppState *app, ManagedService *service);
void refresh_service_row(AppState *app, ManagedService *service);
gboolean poll_services(gpointer user_data);
gboolean poll_services_once(gpointer user_data);

#endif
