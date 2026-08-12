#include "server_manager_internal.h"

gboolean
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
            int width  = 1340;
            int height = 840;
            int x = 60;
            int y = 60;

            SystemParametersInfoW(SPI_GETWORKAREA, 0, &work_area, 0);
            if ((work_area.right - work_area.left) > width) {
                x = work_area.left + ((work_area.right - work_area.left) - width) / 2;
            }
            if ((work_area.bottom - work_area.top) > height) {
                y = work_area.top + ((work_area.bottom - work_area.top) - height) / 2;
            }

            ShowWindow(hwnd, SW_SHOWNORMAL);
            SetWindowPos(hwnd, HWND_TOPMOST,    x, y, width, height, SWP_SHOWWINDOW);
            SetWindowPos(hwnd, HWND_NOTOPMOST,  x, y, width, height, SWP_SHOWWINDOW);
            SetForegroundWindow(hwnd);
            BringWindowToTop(hwnd);
            app->present_attempts++;
            if (app->present_attempts < 6) {
                append_log_level(app, LOG_LEVEL_DEBUG,
                    "[system] present attempt %u hwnd=0x%p",
                    app->present_attempts, hwnd);
                return G_SOURCE_CONTINUE;
            }
        }
        append_log_level(app, LOG_LEVEL_DEBUG,
            "[system] present attempt %u surface ready, hwnd missing",
            app->present_attempts + 1);
    } else {
        app->present_attempts++;
        append_log_level(app, LOG_LEVEL_DEBUG,
            "[system] present attempt %u surface not ready",
            app->present_attempts);
        if (app->present_attempts < 6) {
            return G_SOURCE_CONTINUE;
        }
    }
#endif

    return G_SOURCE_REMOVE;
}

/* ─────────────────────────────────── CSS ─────────────────────────────────── */

void
load_server_manager_css(void)
{
    GtkCssProvider *provider = gtk_css_provider_new();
    const char *css =
        "window.liara-cc-window {"
        "  background: #f0f2f5;"
        "  color: #1f2328;"
        "}"
        ".cc-header {"
        "  background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%);"
        "  padding: 0 18px;"
        "  min-height: 54px;"
        "}"
        ".cc-app-title {"
        "  color: #f1f5f9;"
        "  font-size: 17px;"
        "  font-weight: 800;"
        "  letter-spacing: -0.01em;"
        "}"
        ".cc-app-sub {"
        "  color: #475569;"
        "  font-size: 10px;"
        "  font-weight: 600;"
        "  letter-spacing: 0.06em;"
        "}"
        ".cc-header-summary {"
        "  color: #94a3b8;"
        "  font-size: 12px;"
        "  font-weight: 600;"
        "}"
        ".cc-sidebar {"
        "  background: #1e293b;"
        "  padding: 18px 12px 12px 12px;"
        "  min-width: 196px;"
        "}"
        ".cc-sidebar-section {"
        "  color: #334155;"
        "  font-size: 9px;"
        "  font-weight: 800;"
        "  letter-spacing: 0.16em;"
        "  margin-top: 16px;"
        "  margin-bottom: 5px;"
        "}"
        ".cc-sidebar-info {"
        "  color: #475569;"
        "  font-size: 10px;"
        "}"
        ".cc-sidebar-root {"
        "  color: #334155;"
        "  font-size: 9px;"
        "}"
        ".cc-btn-start-all {"
        "  background: rgba(16,185,129,0.15);"
        "  border: 1px solid rgba(16,185,129,0.30);"
        "  border-radius: 8px;"
        "  color: #6ee7b7;"
        "  font-size: 12px;"
        "  font-weight: 600;"
        "  min-height: 30px;"
        "  margin-bottom: 4px;"
        "}"
        ".cc-btn-start-all:hover {"
        "  background: rgba(16,185,129,0.28);"
        "}"
        ".cc-btn-stop-all {"
        "  background: rgba(239,68,68,0.12);"
        "  border: 1px solid rgba(239,68,68,0.25);"
        "  border-radius: 8px;"
        "  color: #fca5a5;"
        "  font-size: 12px;"
        "  font-weight: 600;"
        "  min-height: 30px;"
        "  margin-bottom: 4px;"
        "}"
        ".cc-btn-stop-all:hover {"
        "  background: rgba(239,68,68,0.22);"
        "}"
        ".cc-btn-restart-all {"
        "  background: rgba(59,130,246,0.12);"
        "  border: 1px solid rgba(59,130,246,0.25);"
        "  border-radius: 8px;"
        "  color: #93c5fd;"
        "  font-size: 12px;"
        "  font-weight: 600;"
        "  min-height: 30px;"
        "  margin-bottom: 4px;"
        "}"
        ".cc-btn-restart-all:hover {"
        "  background: rgba(59,130,246,0.22);"
        "}"
        ".cc-service-card {"
        "  background: #ffffff;"
        "  border: 1px solid #e2e8f0;"
        "  border-radius: 12px;"
        "  padding: 12px 14px;"
        "  box-shadow: 0 1px 3px rgba(15,23,42,0.06), 0 4px 10px rgba(15,23,42,0.04);"
        "}"
        ".cc-service-card:hover {"
        "  border-color: #cbd5e1;"
        "  box-shadow: 0 2px 8px rgba(15,23,42,0.09);"
        "}"
        ".cc-dot {"
        "  font-size: 15px;"
        "  margin-right: 4px;"
        "}"
        ".cc-svc-name {"
        "  color: #0f172a;"
        "  font-size: 14px;"
        "  font-weight: 700;"
        "}"
        ".cc-svc-url {"
        "  color: #94a3b8;"
        "  font-size: 10px;"
        "  font-weight: 500;"
        "  margin-left: 19px;"
        "}"
        ".cc-badge {"
        "  background: #f1f5f9;"
        "  border: 1px solid #e2e8f0;"
        "  border-radius: 5px;"
        "  color: #475569;"
        "  font-size: 10px;"
        "  font-weight: 700;"
        "  padding: 2px 7px;"
        "}"
        ".cc-badge-running {"
        "  background: #dcfce7;"
        "  border-color: #bbf7d0;"
        "  color: #166534;"
        "}"
        ".cc-badge-stopped {"
        "  background: #fff1f2;"
        "  border-color: #fecdd3;"
        "  color: #9f1239;"
        "}"
        ".cc-badge-healthy {"
        "  background: #ecfdf5;"
        "  border-color: #a7f3d0;"
        "  color: #047857;"
        "}"
        ".cc-badge-degraded {"
        "  background: #fffbeb;"
        "  border-color: #fde68a;"
        "  color: #92400e;"
        "}"
        ".cc-btn-start {"
        "  background: linear-gradient(135deg, #10b981 0%, #059669 100%);"
        "  border: 0;"
        "  border-radius: 7px;"
        "  color: white;"
        "  font-size: 11px;"
        "  font-weight: 700;"
        "  min-height: 26px;"
        "  padding: 2px 10px;"
        "}"
        ".cc-btn-stop {"
        "  background: #fff1f2;"
        "  border: 1px solid #fecdd3;"
        "  border-radius: 7px;"
        "  color: #be123c;"
        "  font-size: 11px;"
        "  font-weight: 700;"
        "  min-height: 26px;"
        "  padding: 2px 10px;"
        "}"
        ".cc-btn-restart {"
        "  background: #eff6ff;"
        "  border: 1px solid #bfdbfe;"
        "  border-radius: 7px;"
        "  color: #1d4ed8;"
        "  font-size: 11px;"
        "  font-weight: 700;"
        "  min-height: 26px;"
        "  padding: 2px 10px;"
        "}"
        "notebook.cc-notebook > header {"
        "  background: #1e293b;"
        "  border-bottom: 1px solid #334155;"
        "  padding: 0 6px;"
        "}"
        "notebook.cc-notebook > header > tabs > tab {"
        "  color: #64748b;"
        "  font-size: 10px;"
        "  font-weight: 700;"
        "  letter-spacing: 0.06em;"
        "  padding: 6px 14px;"
        "  border: 0;"
        "}"
        "notebook.cc-notebook > header > tabs > tab:checked {"
        "  color: #e2e8f0;"
        "  border-bottom: 2px solid #3b82f6;"
        "}"
        ".cc-log-view {"
        "  font-family: monospace;"
        "  font-size: 11px;"
        "  background: #0f172a;"
        "  color: #94a3b8;"
        "}"
        ".cc-statusbar {"
        "  background: #0f172a;"
        "  padding: 3px 14px;"
        "  min-height: 22px;"
        "}"
        ".cc-statusbar-text {"
        "  color: #334155;"
        "  font-size: 9px;"
        "  font-weight: 600;"
        "  letter-spacing: 0.04em;"
        "}"
        ".cc-eyebrow {"
        "  color: #64748b;"
        "  font-size: 9px;"
        "  font-weight: 800;"
        "  letter-spacing: 0.14em;"
        "  margin-bottom: 5px;"
        "}"
        "paned > separator {"
        "  background: #e2e8f0;"
        "  min-width: 1px;"
        "  min-height: 1px;"
        "}";

    gtk_css_provider_load_from_string(provider, css);
    gtk_style_context_add_provider_for_display(
        gdk_display_get_default(),
        GTK_STYLE_PROVIDER(provider),
        GTK_STYLE_PROVIDER_PRIORITY_APPLICATION
    );
    g_object_unref(provider);
}

/* ─────────────────────────── button callbacks ─────────────────────────── */

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

void
on_start_all_clicked(GtkButton *button, gpointer user_data)
{
    (void) button;
    start_all_sequential(user_data);
}

void
on_stop_all_clicked(GtkButton *button, gpointer user_data)
{
    (void) button;
    AppState *app = user_data;
    for (int i = 0; i < SERVICE_COUNT; i++) {
        service_stop(app, &app->services[i]);
    }
}

static void
on_restart_all_clicked_cb(GtkButton *button, gpointer user_data)
{
    (void) button;
    AppState *app = user_data;
    for (int i = 0; i < SERVICE_COUNT; i++) {
        service_restart(app, &app->services[i]);
    }
}

/* ─────────────────────── status dot + summary ─────────────────────────── */

void
update_status_summary(AppState *app)
{
    if (app == NULL || app->status_summary_label == NULL) {
        return;
    }

    int running = 0;
    for (int i = 0; i < SERVICE_COUNT; i++) {
        if (service_is_running(&app->services[i])) {
            running++;
        }
    }

    const gchar *color = (running == SERVICE_COUNT)
        ? "#34d399"
        : (running > 0 ? "#fbbf24" : "#f87171");

    g_autofree gchar *markup = g_markup_printf_escaped(
        "<span foreground=\"%s\" weight=\"bold\">%d / %d</span>"
        "<span foreground=\"#64748b\"> running</span>",
        color, running, SERVICE_COUNT
    );
    gtk_label_set_markup(app->status_summary_label, markup);
}

void
update_status_dot(AppState *app, ManagedService *service)
{
    if (service->status_dot == NULL) {
        return;
    }

    gboolean running = service_is_running(service);
    const gchar *color;

    if (!running) {
        color = "#64748b";
    } else if (!service->last_health_ok) {
        color = "#f59e0b";
    } else {
        color = "#22c55e";
    }

    g_autofree gchar *markup = g_markup_printf_escaped(
        "<span foreground=\"%s\">&#9679;</span>", color
    );
    gtk_label_set_markup(service->status_dot, markup);
    update_status_summary(app);
}

/* ────────────────────────── service card row ───────────────────────────── */

GtkWidget *
build_service_row(AppState *app, ManagedService *service, int index)
{
    GtkWidget *card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 8);
    gtk_widget_add_css_class(card, "cc-service-card");

    GtkWidget *top = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_widget_set_hexpand(top, TRUE);

    /* left: dot + name + url */
    GtkWidget *left     = gtk_box_new(GTK_ORIENTATION_VERTICAL, 2);
    GtkWidget *name_row = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 5);
    GtkWidget *dot      = gtk_label_new(NULL);
    GtkWidget *name_lbl = gtk_label_new(service->name);
    GtkWidget *url_lbl  = gtk_label_new(service->health_url);

    gtk_widget_set_hexpand(left, TRUE);
    gtk_widget_add_css_class(dot,      "cc-dot");
    gtk_widget_add_css_class(name_lbl, "cc-svc-name");
    gtk_widget_add_css_class(url_lbl,  "cc-svc-url");
    gtk_label_set_xalign(GTK_LABEL(name_lbl), 0.0f);
    gtk_label_set_xalign(GTK_LABEL(url_lbl),  0.0f);
    gtk_label_set_markup(GTK_LABEL(dot),
        "<span foreground=\"#64748b\">&#9679;</span>");

    gtk_box_append(GTK_BOX(name_row), dot);
    gtk_box_append(GTK_BOX(name_row), name_lbl);
    gtk_box_append(GTK_BOX(left), name_row);
    gtk_box_append(GTK_BOX(left), url_lbl);

    /* middle: status badges */
    GtkWidget *badges = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 6);
    gtk_widget_set_halign(badges, GTK_ALIGN_CENTER);
    gtk_widget_set_valign(badges, GTK_ALIGN_CENTER);
    gtk_widget_set_margin_start(badges, 12);
    gtk_widget_set_margin_end(badges, 12);

    GtkWidget *proc_lbl   = gtk_label_new(NULL);
    GtkWidget *pid_lbl    = gtk_label_new(NULL);
    GtkWidget *health_lbl = gtk_label_new(NULL);

    gtk_widget_add_css_class(proc_lbl,   "cc-badge");
    gtk_widget_add_css_class(pid_lbl,    "cc-badge");
    gtk_widget_add_css_class(health_lbl, "cc-badge");

    gtk_box_append(GTK_BOX(badges), proc_lbl);
    gtk_box_append(GTK_BOX(badges), pid_lbl);
    gtk_box_append(GTK_BOX(badges), health_lbl);

    /* right: action buttons */
    GtkWidget *actions     = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 5);
    GtkWidget *start_btn   = gtk_button_new_with_label("Start");
    GtkWidget *stop_btn    = gtk_button_new_with_label("Stop");
    GtkWidget *restart_btn = gtk_button_new_with_label("Restart");

    gtk_widget_set_halign(actions, GTK_ALIGN_END);
    gtk_widget_set_valign(actions, GTK_ALIGN_CENTER);
    gtk_widget_add_css_class(start_btn,   "cc-btn-start");
    gtk_widget_add_css_class(stop_btn,    "cc-btn-stop");
    gtk_widget_add_css_class(restart_btn, "cc-btn-restart");

    g_object_set_data(G_OBJECT(start_btn),   "svc-index", GINT_TO_POINTER(index));
    g_object_set_data(G_OBJECT(stop_btn),    "svc-index", GINT_TO_POINTER(index));
    g_object_set_data(G_OBJECT(restart_btn), "svc-index", GINT_TO_POINTER(index));

    g_signal_connect(start_btn,   "clicked", G_CALLBACK(on_start_clicked),   app);
    g_signal_connect(stop_btn,    "clicked", G_CALLBACK(on_stop_clicked),    app);
    g_signal_connect(restart_btn, "clicked", G_CALLBACK(on_restart_clicked), app);

    gtk_box_append(GTK_BOX(actions), start_btn);
    gtk_box_append(GTK_BOX(actions), stop_btn);
    gtk_box_append(GTK_BOX(actions), restart_btn);

    gtk_box_append(GTK_BOX(top), left);
    gtk_box_append(GTK_BOX(top), badges);
    gtk_box_append(GTK_BOX(top), actions);
    gtk_box_append(GTK_BOX(card), top);

    service->status_dot     = GTK_LABEL(dot);
    service->process_label  = GTK_LABEL(proc_lbl);
    service->pid_label      = GTK_LABEL(pid_lbl);
    service->health_label   = GTK_LABEL(health_lbl);
    service->last_health_ok = TRUE;

    refresh_service_row(app, service);
    return card;
}

/* ───────────────────────────── sidebar ────────────────────────────────── */

GtkWidget *
build_sidebar(AppState *app)
{
    GtkWidget *sidebar = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_widget_add_css_class(sidebar, "cc-sidebar");

    /* app name + version */
    GtkWidget *app_name = gtk_label_new("LIARA");
    gtk_label_set_xalign(GTK_LABEL(app_name), 0.0f);
    gtk_widget_add_css_class(app_name, "cc-app-title");

    g_autofree gchar *ver_text = g_strdup_printf("Control Center  %s",
        LIARA_SERVER_MANAGER_VERSION);
    GtkWidget *app_ver = gtk_label_new(ver_text);
    gtk_label_set_xalign(GTK_LABEL(app_ver), 0.0f);
    gtk_widget_add_css_class(app_ver, "cc-sidebar-info");
    gtk_widget_set_margin_bottom(app_ver, 10);

    gtk_box_append(GTK_BOX(sidebar), app_name);
    gtk_box_append(GTK_BOX(sidebar), app_ver);

    /* service controls */
    GtkWidget *svc_lbl = gtk_label_new("SERVICES");
    gtk_label_set_xalign(GTK_LABEL(svc_lbl), 0.0f);
    gtk_widget_add_css_class(svc_lbl, "cc-sidebar-section");

    GtkWidget *start_btn   = gtk_button_new_with_label("▶  Start All");
    GtkWidget *stop_btn    = gtk_button_new_with_label("■  Stop All");
    GtkWidget *restart_btn = gtk_button_new_with_label("↺  Restart All");

    gtk_widget_add_css_class(start_btn,   "cc-btn-start-all");
    gtk_widget_add_css_class(stop_btn,    "cc-btn-stop-all");
    gtk_widget_add_css_class(restart_btn, "cc-btn-restart-all");

    g_signal_connect(start_btn,   "clicked", G_CALLBACK(on_start_all_clicked),     app);
    g_signal_connect(stop_btn,    "clicked", G_CALLBACK(on_stop_all_clicked),      app);
    g_signal_connect(restart_btn, "clicked", G_CALLBACK(on_restart_all_clicked_cb), app);

    gtk_box_append(GTK_BOX(sidebar), svc_lbl);
    gtk_box_append(GTK_BOX(sidebar), start_btn);
    gtk_box_append(GTK_BOX(sidebar), stop_btn);
    gtk_box_append(GTK_BOX(sidebar), restart_btn);

    /* status summary */
    GtkWidget *status_lbl = gtk_label_new("STATUS");
    gtk_label_set_xalign(GTK_LABEL(status_lbl), 0.0f);
    gtk_widget_add_css_class(status_lbl, "cc-sidebar-section");

    GtkWidget *summary = gtk_label_new(NULL);
    gtk_label_set_xalign(GTK_LABEL(summary), 0.0f);
    gtk_widget_add_css_class(summary, "cc-sidebar-info");
    gtk_label_set_markup(GTK_LABEL(summary),
        "<span foreground=\"#475569\">0 / 3 running</span>");

    gtk_box_append(GTK_BOX(sidebar), status_lbl);
    gtk_box_append(GTK_BOX(sidebar), summary);

    /* spacer */
    GtkWidget *spacer = gtk_box_new(GTK_ORIENTATION_VERTICAL, 0);
    gtk_widget_set_vexpand(spacer, TRUE);
    gtk_box_append(GTK_BOX(sidebar), spacer);

    /* project root at bottom */
    GtkWidget *sys_lbl = gtk_label_new("SYSTEM");
    gtk_label_set_xalign(GTK_LABEL(sys_lbl), 0.0f);
    gtk_widget_add_css_class(sys_lbl, "cc-sidebar-section");

    g_autofree gchar *root_display = NULL;
    if (app->project_root != NULL) {
        g_autofree gchar *base        = g_path_get_basename(app->project_root);
        g_autofree gchar *parent_full = g_path_get_dirname(app->project_root);
        g_autofree gchar *parent_base = g_path_get_basename(parent_full);
        root_display = g_strdup_printf(".../%s/%s", parent_base, base);
    } else {
        root_display = g_strdup("-");
    }

    GtkWidget *root_lbl = gtk_label_new(root_display);
    gtk_label_set_xalign(GTK_LABEL(root_lbl), 0.0f);
    gtk_label_set_ellipsize(GTK_LABEL(root_lbl), PANGO_ELLIPSIZE_START);
    gtk_widget_add_css_class(root_lbl, "cc-sidebar-root");

    gtk_box_append(GTK_BOX(sidebar), sys_lbl);
    gtk_box_append(GTK_BOX(sidebar), root_lbl);

    app->status_summary_label = GTK_LABEL(summary);
    return sidebar;
}

/* ─────────────────────────── log notebook ──────────────────────────────── */

GtkWidget *
build_log_notebook(AppState *app)
{
    GtkWidget *notebook = gtk_notebook_new();
    gtk_widget_add_css_class(notebook, "cc-notebook");
    gtk_widget_set_vexpand(notebook, TRUE);
    gtk_widget_set_hexpand(notebook, TRUE);

    /* System / global log */
    {
        GtkWidget *tv = gtk_text_view_new();
        gtk_text_view_set_editable(GTK_TEXT_VIEW(tv), FALSE);
        gtk_text_view_set_cursor_visible(GTK_TEXT_VIEW(tv), FALSE);
        gtk_text_view_set_wrap_mode(GTK_TEXT_VIEW(tv), GTK_WRAP_WORD_CHAR);
        gtk_text_view_set_monospace(GTK_TEXT_VIEW(tv), TRUE);
        gtk_widget_add_css_class(tv, "cc-log-view");
        app->log_buffer = gtk_text_view_get_buffer(GTK_TEXT_VIEW(tv));

        GtkWidget *sw = gtk_scrolled_window_new();
        gtk_widget_set_vexpand(sw, TRUE);
        gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(sw), tv);
        gtk_notebook_append_page(GTK_NOTEBOOK(notebook), sw, gtk_label_new("System"));
    }

    /* Per-service log tabs */
    const gchar *tab_names[SERVICE_COUNT] = {"API", "Memory", "Embedding"};
    for (int i = 0; i < SERVICE_COUNT; i++) {
        ManagedService *svc = &app->services[i];
        GtkTextBuffer *buf  = gtk_text_buffer_new(NULL);
        GtkWidget *tv       = gtk_text_view_new_with_buffer(buf);
        gtk_text_view_set_editable(GTK_TEXT_VIEW(tv), FALSE);
        gtk_text_view_set_cursor_visible(GTK_TEXT_VIEW(tv), FALSE);
        gtk_text_view_set_wrap_mode(GTK_TEXT_VIEW(tv), GTK_WRAP_WORD_CHAR);
        gtk_text_view_set_monospace(GTK_TEXT_VIEW(tv), TRUE);
        gtk_widget_add_css_class(tv, "cc-log-view");

        svc->service_log_buffer = buf;
        svc->service_log_view   = GTK_TEXT_VIEW(tv);

        GtkWidget *sw = gtk_scrolled_window_new();
        gtk_widget_set_vexpand(sw, TRUE);
        gtk_scrolled_window_set_child(GTK_SCROLLED_WINDOW(sw), tv);
        gtk_notebook_append_page(GTK_NOTEBOOK(notebook), sw,
            gtk_label_new(tab_names[i]));
    }

    app->log_notebook = GTK_NOTEBOOK(notebook);
    return notebook;
}
