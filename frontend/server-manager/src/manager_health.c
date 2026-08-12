#include "server_manager_internal.h"

gboolean
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

void
update_health_label(ManagedService *service, gboolean healthy, const gchar *detail)
{
    gboolean running = service_is_running(service);
    const gchar *display = (detail != NULL && detail[0] != '\0') ? detail : "down";

    if (healthy) {
        g_autofree gchar *text = g_markup_printf_escaped(
            "<span foreground=\"#047857\" weight=\"700\">&#9679; HEALTH  %s</span>",
            display
        );
        gtk_label_set_markup(service->health_label, text);
        gtk_widget_remove_css_class(GTK_WIDGET(service->health_label), "cc-badge-stopped");
        gtk_widget_remove_css_class(GTK_WIDGET(service->health_label), "cc-badge-degraded");
        gtk_widget_add_css_class(GTK_WIDGET(service->health_label), "cc-badge-healthy");
    } else if (running) {
        g_autofree gchar *text = g_markup_printf_escaped(
            "<span foreground=\"#92400e\" weight=\"700\">&#9679; HEALTH  %s</span>",
            display
        );
        gtk_label_set_markup(service->health_label, text);
        gtk_widget_remove_css_class(GTK_WIDGET(service->health_label), "cc-badge-healthy");
        gtk_widget_remove_css_class(GTK_WIDGET(service->health_label), "cc-badge-stopped");
        gtk_widget_add_css_class(GTK_WIDGET(service->health_label), "cc-badge-degraded");
    } else {
        g_autofree gchar *text = g_markup_printf_escaped(
            "<span foreground=\"#9f1239\" weight=\"700\">&#9679; HEALTH  %s</span>",
            display
        );
        gtk_label_set_markup(service->health_label, text);
        gtk_widget_remove_css_class(GTK_WIDGET(service->health_label), "cc-badge-healthy");
        gtk_widget_remove_css_class(GTK_WIDGET(service->health_label), "cc-badge-degraded");
        gtk_widget_add_css_class(GTK_WIDGET(service->health_label), "cc-badge-stopped");
    }
}

void
apply_health_result(AppState *app, ManagedService *service, gboolean healthy, const gchar *detail)
{
    service->last_health_ok = healthy;
    if (app->is_shutting_down) {
        return;
    }

    if (healthy) {
        service->health_fail_streak = 0;
        update_health_label(service, TRUE, detail);
        update_status_dot(app, service);
        return;
    }

    if (!service_is_running(service)) {
        service->health_fail_streak = 0;
        update_health_label(service, FALSE, detail);
        update_status_dot(app, service);
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
    update_status_dot(app, service);
}

void
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
                    apply_health_result(app, service, status_is_healthy(status), status != NULL ? status : "up");
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

                    apply_health_result(app, service, status_is_healthy(nested_status), nested_status != NULL ? nested_status : "up");
                    return;
                }
            }
        }
    }

    apply_health_result(app, service, TRUE, "up");
}

void
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

void
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
                append_log_level(app, LOG_LEVEL_INFO, "[%s] restart after %s (code %d)", service->key, reason, has_exit_code ? exit_code : 0);
                service_start(app, service);
                running = service_is_running(service);
            } else {
                append_log_level(app, LOG_LEVEL_ERROR, "[%s] restart canceled due to non-zero exit (code %d)", service->key, has_exit_code ? exit_code : -1);
            }
        }
    }

    if (running) {
        g_autofree gchar *pid_markup = NULL;
        /* process badge */
        gtk_label_set_markup(service->process_label,
            "<span weight=\"700\">RUNNING</span>");
        gtk_widget_remove_css_class(GTK_WIDGET(service->process_label), "cc-badge-stopped");
        gtk_widget_add_css_class(GTK_WIDGET(service->process_label), "cc-badge-running");
        /* pid badge */
        if (service->process != NULL) {
            const gchar *identifier = g_subprocess_get_identifier(service->process);
            pid_markup = g_markup_printf_escaped(
                "<span weight=\"700\">PID %s</span>",
                identifier != NULL ? identifier : "?"
            );
        } else if (service->tracked_pid > 0) {
            pid_markup = g_markup_printf_escaped(
                "<span weight=\"700\">PID %d</span>",
                service->tracked_pid
            );
        } else {
            pid_markup = g_markup_printf_escaped("<span weight=\"700\">PID ?</span>");
        }
        gtk_label_set_markup(service->pid_label, pid_markup);
    } else {
        gtk_label_set_markup(service->process_label,
            "<span weight=\"700\">STOPPED</span>");
        gtk_widget_remove_css_class(GTK_WIDGET(service->process_label), "cc-badge-running");
        gtk_widget_add_css_class(GTK_WIDGET(service->process_label), "cc-badge-stopped");
        gtk_label_set_markup(service->pid_label, "<span weight=\"700\">PID -</span>");
    }

    update_status_dot(app, service);
    fire_health_probe_async(app, service);
}

gboolean
poll_services(gpointer user_data)
{
    AppState *app = user_data;

    for (int i = 0; i < SERVICE_COUNT; i++) {
        refresh_service_row(app, &app->services[i]);
    }

    return G_SOURCE_CONTINUE;
}

gboolean
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
