#include "server_manager_internal.h"

static gboolean
service_has_pid_file(ManagedService *service)
{
    return service->pid_file_path != NULL &&
           g_file_test(service->pid_file_path, G_FILE_TEST_IS_REGULAR);
}

static gboolean
pid_is_alive(gint pid)
{
    if (pid <= 0) {
        return FALSE;
    }

#ifdef G_OS_WIN32
    HANDLE process_handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, (DWORD) pid);
    if (process_handle != NULL) {
        DWORD exit_code = 0;
        gboolean alive = GetExitCodeProcess(process_handle, &exit_code) && exit_code == STILL_ACTIVE;
        CloseHandle(process_handle);
        return alive;
    }
    return FALSE;
#else
    return kill(pid, 0) == 0;
#endif
}

gboolean
service_is_running(ManagedService *service)
{
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

    if (service->tracked_pid > 0) {
        if (pid_is_alive(service->tracked_pid)) {
            return TRUE;
        }
        return FALSE;
    }

    if (service->process != NULL) {
        /* No PID available yet; assume the owned subprocess is still live until proven otherwise. */
        return TRUE;
    }

    return FALSE;
}

gchar *
build_service_pid_file_path(AppState *app, const gchar *service_key)
{
    const gchar *cache_root = g_getenv("XDG_CACHE_HOME");
    if (cache_root != NULL && cache_root[0] != '\0') {
        return g_build_filename(cache_root, g_strdup_printf("%s.pid", service_key), NULL);
    }

    return g_build_filename(app->project_root, "cache", g_strdup_printf("%s.pid", service_key), NULL);
}

void
clear_service_pid_tracking(ManagedService *service)
{
    service->tracked_pid = 0;
    service->run_source = RUN_SOURCE_STOPPED;
    if (service->pid_file_path != NULL && g_file_test(service->pid_file_path, G_FILE_TEST_EXISTS)) {
        g_remove(service->pid_file_path);
    }
}

void
write_service_pid_file(AppState *app, ManagedService *service, gint pid)
{
    g_autofree gchar *pid_text = NULL;
    g_autofree gchar *pid_dir = NULL;

    service->tracked_pid = pid;
    service->run_source = RUN_SOURCE_CACHED_PID;
    if (service->pid_file_path == NULL) {
        return;
    }

    pid_dir = g_path_get_dirname(service->pid_file_path);
    g_mkdir_with_parents(pid_dir, 0755);
    pid_text = g_strdup_printf("%d\n", pid);
    if (!g_file_set_contents(service->pid_file_path, pid_text, -1, NULL)) {
        append_log_level(app, LOG_LEVEL_WARN, "[%s] could not write pid file: %s", service->key, service->pid_file_path);
    }
}

void
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

gboolean
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

gint
service_port_from_health_url(const gchar *health_url)
{
    g_autoptr(GError) error = NULL;
    g_autoptr(GUri) uri = g_uri_parse(health_url, G_URI_FLAGS_NONE, &error);
    if (uri == NULL) {
        return -1;
    }

    return g_uri_get_port(uri);
}

gboolean
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

#ifdef G_OS_WIN32
static gint
discover_listening_pid_windows(gint port)
{
    g_autofree gchar *command = NULL;
    g_autofree gchar *stdout_str = NULL;
    g_autofree gchar *stderr_str = NULL;
    g_autoptr(GError) error = NULL;
    gint exit_status = 0;

    command = g_strdup_printf(
        "powershell -NoProfile -Command \"$pid=(Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort %d -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess); if($pid){Write-Output $pid}\"",
        port
    );

    if (!g_spawn_command_line_sync(command, &stdout_str, &stderr_str, &exit_status, &error)) {
        return 0;
    }

    if (stdout_str == NULL) {
        return 0;
    }

    g_strstrip(stdout_str);
    if (stdout_str[0] == '\0') {
        return 0;
    }

    gchar *end_ptr = NULL;
    long parsed = strtol(stdout_str, &end_ptr, 10);
    if (end_ptr == stdout_str || parsed <= 0 || parsed > G_MAXINT) {
        return 0;
    }

    return (gint) parsed;
}

static gboolean
process_commandline_matches_windows(gint pid, const gchar *match_token)
{
    g_autofree gchar *command = NULL;
    g_autofree gchar *stdout_str = NULL;
    g_autofree gchar *stderr_str = NULL;
    g_autoptr(GError) error = NULL;
    gint exit_status = 0;

    if (match_token == NULL || match_token[0] == '\0') {
        return TRUE;
    }

    command = g_strdup_printf(
        "powershell -NoProfile -Command \"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = %d' -ErrorAction SilentlyContinue; if($p){Write-Output $p.CommandLine}\"",
        pid
    );

    if (!g_spawn_command_line_sync(command, &stdout_str, &stderr_str, &exit_status, &error)) {
        return FALSE;
    }

    if (stdout_str == NULL) {
        return FALSE;
    }

    g_strstrip(stdout_str);
    if (stdout_str[0] == '\0') {
        return FALSE;
    }

    return g_strrstr(stdout_str, match_token) != NULL;
}
#endif

gboolean
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
    gint discovered_pid = discover_listening_pid_windows(port);

    if (discovered_pid <= 0) {
        return FALSE;
    }

    if (process_commandline_matches_windows(discovered_pid, service->process_match_token)) {
        write_service_pid_file(app, service, discovered_pid);
        append_log_level(app, LOG_LEVEL_INFO, "[%s] discovered running pid %d from port %d", service->key, service->tracked_pid, port);
        return TRUE;
    }
    append_log_level(app, LOG_LEVEL_DEBUG, "[%s] port %d owned by pid %d but commandline did not match", service->key, port, discovered_pid);
#endif

    return FALSE;
}

gboolean
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

void
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
        service_log_append(service, line);
    }

    schedule_log_read(app, service);
    g_free(context);
}

void
schedule_log_read(AppState *app, ManagedService *service)
{
    LogReadContext *context;

    if (app->is_shutting_down || service->log_stream == NULL) {
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

void
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
        if (recover_pid_from_port_if_matching(app, service) && service_is_running(service)) {
            append_log_level(
                app,
                LOG_LEVEL_WARN,
                "[%s] startup guard: port %d already used by matching process (pid %d), adopting existing service",
                service->key,
                target_port,
                service->tracked_pid
            );
            return;
        }
        append_log_level(app, LOG_LEVEL_ERROR, "[%s] start skipped: port %d already in use (startup guard)", service->key, target_port);
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

void
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

void
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

void
start_all_sequential(AppState *app)
{
    if (SERVICE_COUNT == 0) {
        return;
    }
    service_start(app, &app->services[0]);
    if (SERVICE_COUNT <= 1 || app->start_delay_ms == 0) {
        for (int i = 1; i < SERVICE_COUNT; i++) {
            service_start(app, &app->services[i]);
        }
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
