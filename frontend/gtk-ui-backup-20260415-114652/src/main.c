#include <gtk/gtk.h>

#include "liara_window.h"

static void
load_css(void)
{
    GtkCssProvider *provider = gtk_css_provider_new();
    g_autofree char *exe_dir = g_path_get_dirname(g_get_prgname());
    g_autofree char *dist_dir = NULL;
    g_autofree char *css_path = NULL;
    GFile *file;
    GError *error = NULL;

    if (g_path_is_absolute(g_get_prgname())) {
        dist_dir = g_path_get_dirname(exe_dir);
        css_path = g_build_filename(dist_dir, "config", "style.css", NULL);
    } else {
        g_autofree char *cwd = g_get_current_dir();
        css_path = g_build_filename(cwd, "style.css", NULL);
    }

    file = g_file_new_for_path(css_path);
    gtk_css_provider_load_from_file(provider, file);
    gtk_style_context_add_provider_for_display(
        gdk_display_get_default(),
        GTK_STYLE_PROVIDER(provider),
        GTK_STYLE_PROVIDER_PRIORITY_APPLICATION
    );

    if (error != NULL) {
        g_error_free(error);
    }

    g_object_unref(file);
    g_object_unref(provider);
}

static void
on_activate(GtkApplication *app, gpointer user_data)
{
    GtkWidget *window = liara_window_new(app);
    (void) user_data;
    load_css();
    gtk_window_present(GTK_WINDOW(window));
}

int
main(int argc, char **argv)
{
    GtkApplication *app = gtk_application_new("ai.liara.gtkui", G_APPLICATION_DEFAULT_FLAGS);
    g_autofree char *program_path = NULL;
    int status;

    g_setenv("GTK_USE_PORTAL", "0", TRUE);
    if (argc > 0 && argv[0] != NULL) {
        if (g_path_is_absolute(argv[0])) {
            program_path = g_strdup(argv[0]);
        } else {
            g_autofree char *cwd = g_get_current_dir();
            program_path = g_canonicalize_filename(argv[0], cwd);
        }
        g_set_prgname(program_path);
    }
    g_signal_connect(app, "activate", G_CALLBACK(on_activate), NULL);
    status = g_application_run(G_APPLICATION(app), argc, argv);
    g_object_unref(app);
    return status;
}
