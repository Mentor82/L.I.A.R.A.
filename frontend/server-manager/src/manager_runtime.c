#include "server_manager_internal.h"

static gboolean
directory_has_services(const gchar *path)
{
    g_autofree gchar *services_path = g_build_filename(path, "services", NULL);
    return g_file_test(services_path, G_FILE_TEST_IS_DIR);
}

gchar *
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

gchar *
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

gchar **
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

void
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

void
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

void
on_activate(GtkApplication *application, gpointer user_data)
{
    (void) user_data;

    AppState *app = g_new0(AppState, 1);
    app->application = application;
    g_object_set_data_full(G_OBJECT(application), "liara-app-state", app, free_app_state);
    app->project_root = find_project_root();
    app->python_exe   = resolve_python_executable(app->project_root);
    app->log_file_path = build_default_log_file_path();
    ensure_log_file_exists(app);
    app->http_session = soup_session_new_with_options(
        "timeout", 2, "idle-timeout", 2, NULL);
    load_server_manager_config(app);
    initialize_services(app);
    load_server_manager_css();
    g_message("LIARA Control Center  project_root=%s", app->project_root);

    /* ── window ── */
    app->window = GTK_WINDOW(gtk_application_window_new(application));
    gtk_window_set_title(app->window, "LIARA Control Center");
    gtk_window_set_default_size(app->window, 1340, 840);
    gtk_widget_add_css_class(GTK_WIDGET(app->window), "liara-cc-window");

    /* ── outer root: VBox (header + body + statusbar) ── */
    GtkWidget *outer = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);

    /* ── header strip ── */
    GtkWidget *header_strip = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_add_css_class(header_strip, "cc-header");
    gtk_widget_set_hexpand(header_strip, TRUE);

    GtkWidget *hdr_left  = gtk_box_new(GTK_ORIENTATION_VERTICAL, 1);
    gtk_widget_set_valign(hdr_left, GTK_ALIGN_CENTER);
    gtk_widget_set_hexpand(hdr_left, TRUE);
    gtk_widget_set_margin_top(hdr_left, 10);
    gtk_widget_set_margin_bottom(hdr_left, 10);

    GtkWidget *hdr_title = gtk_label_new("LIARA Control Center");
    gtk_label_set_xalign(GTK_LABEL(hdr_title), 0.0f);
    gtk_widget_add_css_class(hdr_title, "cc-app-title");

    GtkWidget *hdr_sub = gtk_label_new("Service orchestration  ·  API · Memory · Embedding");
    gtk_label_set_xalign(GTK_LABEL(hdr_sub), 0.0f);
    gtk_widget_add_css_class(hdr_sub, "cc-app-sub");

    gtk_box_append(GTK_BOX(hdr_left), hdr_title);
    gtk_box_append(GTK_BOX(hdr_left), hdr_sub);

    GtkWidget *hdr_right = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_widget_set_valign(hdr_right, GTK_ALIGN_CENTER);
    gtk_widget_set_margin_end(hdr_right, 4);

    /* header summary is set by update_status_summary; store in AppState later */
    GtkWidget *hdr_summary = gtk_label_new(NULL);
    gtk_label_set_markup(GTK_LABEL(hdr_summary),
        "<span foreground=\"#f87171\" weight=\"bold\">0 / 3</span>"
        "<span foreground=\"#64748b\"> running</span>");
    gtk_widget_add_css_class(hdr_summary, "cc-header-summary");
    gtk_box_append(GTK_BOX(hdr_right), hdr_summary);

    gtk_box_append(GTK_BOX(header_strip), hdr_left);
    gtk_box_append(GTK_BOX(header_strip), hdr_right);

    /* ── body: horizontal paned (sidebar | main) ── */
    GtkWidget *paned_h = gtk_paned_new(GTK_ORIENTATION_HORIZONTAL);
    gtk_paned_set_position(GTK_PANED(paned_h), 210);
    gtk_widget_set_vexpand(paned_h, TRUE);

    /* sidebar */
    GtkWidget *sidebar = build_sidebar(app);
    gtk_paned_set_start_child(GTK_PANED(paned_h), sidebar);
    gtk_paned_set_resize_start_child(GTK_PANED(paned_h), FALSE);
    gtk_paned_set_shrink_start_child(GTK_PANED(paned_h), FALSE);

    /* main area: vertical paned (services | log notebook) */
    GtkWidget *paned_v = gtk_paned_new(GTK_ORIENTATION_VERTICAL);
    gtk_paned_set_position(GTK_PANED(paned_v), 390);
    gtk_widget_set_hexpand(paned_v, TRUE);
    gtk_widget_set_vexpand(paned_v, TRUE);

    /* services panel */
    GtkWidget *services_wrapper = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_widget_set_margin_top(services_wrapper, 12);
    gtk_widget_set_margin_start(services_wrapper, 14);
    gtk_widget_set_margin_end(services_wrapper, 14);
    gtk_widget_set_margin_bottom(services_wrapper, 6);

    GtkWidget *svc_eyebrow = gtk_label_new("SERVICES");
    gtk_label_set_xalign(GTK_LABEL(svc_eyebrow), 0.0f);
    gtk_widget_add_css_class(svc_eyebrow, "cc-eyebrow");
    gtk_widget_set_margin_bottom(svc_eyebrow, 6);
    gtk_box_append(GTK_BOX(services_wrapper), svc_eyebrow);

    GtkWidget *services_box = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    for (int i = 0; i < SERVICE_COUNT; i++) {
        GtkWidget *row = build_service_row(app, &app->services[i], i);
        gtk_box_append(GTK_BOX(services_box), row);
    }

    GtkWidget *svc_scroll = gtk_scrolled_window_new();
    gtk_widget_set_vexpand(svc_scroll, TRUE);
    gtk_widget_set_hexpand(svc_scroll, TRUE);
    gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(svc_scroll), services_box);

    gtk_box_append(GTK_BOX(services_wrapper), svc_scroll);
    gtk_paned_set_start_child(GTK_PANED(paned_v), services_wrapper);
    gtk_paned_set_resize_start_child(GTK_PANED(paned_v), TRUE);
    gtk_paned_set_shrink_start_child(GTK_PANED(paned_v), FALSE);

    /* log notebook panel — build_log_notebook assigns app->log_buffer + per-svc buffers */
    GtkWidget *log_nb = build_log_notebook(app);
    gtk_paned_set_end_child(GTK_PANED(paned_v), log_nb);
    gtk_paned_set_resize_end_child(GTK_PANED(paned_v), TRUE);
    gtk_paned_set_shrink_end_child(GTK_PANED(paned_v), FALSE);

    gtk_paned_set_end_child(GTK_PANED(paned_h), paned_v);

    /* also wire the header summary label to app->status_summary_label
       (build_sidebar already set it; share the same pointer for the header too) */
    /* We use the sidebar summary label as canonical — hdr_summary stays separate */

    /* status bar */
    GtkWidget *statusbar = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 10);
    gtk_widget_add_css_class(statusbar, "cc-statusbar");

    g_autofree gchar *sb_text = g_strdup_printf(
        "LIARA Control Center  %s  |  %s",
        LIARA_SERVER_MANAGER_VERSION,
        app->project_root != NULL ? app->project_root : "-"
    );
    GtkWidget *sb_lbl = gtk_label_new(sb_text);
    gtk_label_set_xalign(GTK_LABEL(sb_lbl), 0.0f);
    gtk_widget_add_css_class(sb_lbl, "cc-statusbar-text");
    gtk_box_append(GTK_BOX(statusbar), sb_lbl);

    /* assemble outer */
    gtk_box_append(GTK_BOX(outer), header_strip);
    gtk_box_append(GTK_BOX(outer), paned_h);
    gtk_box_append(GTK_BOX(outer), statusbar);

    gtk_window_set_child(app->window, outer);
    g_signal_connect(app->window, "destroy", G_CALLBACK(on_window_destroy), app);

    append_log_level(app, LOG_LEVEL_INFO, "[system] LIARA Control Center  version=%s",
        LIARA_SERVER_MANAGER_VERSION);
    append_log_level(app, LOG_LEVEL_INFO, "[system] project_root=%s  python=%s",
        app->project_root, app->python_exe);
    append_log_level(app, LOG_LEVEL_INFO, "[system] config=%s  env=%s",
        app->config_path != NULL ? app->config_path : "(none)",
        app->env_file_path != NULL ? app->env_file_path : "(none)");
    append_log_level(app, LOG_LEVEL_INFO, "[system] autostart=%s  log_level=%s",
        app->autostart_enabled ? "on" : "off",
        log_level_to_string(app->log_level));
    append_log_level(app, LOG_LEVEL_INFO, "[system] C GUI ready");

    /* initial summary */
    update_status_summary(app);

    gtk_widget_set_visible(GTK_WIDGET(app->window), TRUE);
    gtk_window_present(app->window);
    app->present_attempts    = 0;
    app->present_source_id   = g_timeout_add(300, ensure_window_foreground, app);
    app->poll_once_source_id = g_timeout_add(150, poll_services_once, app);
    app->poll_source_id      = g_timeout_add_seconds(POLL_INTERVAL_SECONDS, poll_services, app);
    if (app->autostart_enabled) {
        append_log_level(app, LOG_LEVEL_INFO,
            "[system] autostart: starting services (delay %ums)", app->start_delay_ms);
        start_all_sequential(app);
    }
}
