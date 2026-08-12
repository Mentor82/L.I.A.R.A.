#include "server_manager_internal.h"

const gchar *
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

LogLevel
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

void
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

void
service_log_append(ManagedService *service, const gchar *line)
{
    GtkTextIter end_iter;
    GtkTextIter start_iter;
    gint line_count;

    if (service == NULL || service->service_log_buffer == NULL || line == NULL) {
        return;
    }

    gtk_text_buffer_get_end_iter(service->service_log_buffer, &end_iter);
    gtk_text_buffer_insert(service->service_log_buffer, &end_iter, line, -1);
    gtk_text_buffer_insert(service->service_log_buffer, &end_iter, "\n", -1);

    /* Keep buffer bounded to last 2000 lines */
    line_count = gtk_text_buffer_get_line_count(service->service_log_buffer);
    if (line_count > 2000) {
        gtk_text_buffer_get_start_iter(service->service_log_buffer, &start_iter);
        GtkTextIter trim_end;
        gtk_text_buffer_get_iter_at_line(service->service_log_buffer, &trim_end, line_count - 2000);
        gtk_text_buffer_delete(service->service_log_buffer, &start_iter, &trim_end);
    }

    /* Auto-scroll to end if view is visible */
    if (service->service_log_view != NULL &&
        gtk_widget_get_visible(GTK_WIDGET(service->service_log_view))) {
        gtk_text_buffer_get_end_iter(service->service_log_buffer, &end_iter);
        GtkTextMark *mark = gtk_text_buffer_get_insert(service->service_log_buffer);
        gtk_text_buffer_place_cursor(service->service_log_buffer, &end_iter);
        gtk_text_view_scroll_to_mark(service->service_log_view, mark, 0.0, FALSE, 0.0, 1.0);
    }
}
