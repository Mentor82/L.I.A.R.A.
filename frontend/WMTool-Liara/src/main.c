#include <gtk/gtk.h>

#include "liara_window.h"

typedef struct {
    gboolean dev_mode;
} LiaraLaunchConfig;

static gboolean
is_dev_mode_enabled(int argc, char **argv)
{
    const char *dev_mode_flag = g_getenv("LIARA_DEV_MODE");
    const char *expected_password = g_getenv("LIARA_DEV_PASSWORD");
    int i;

    if (dev_mode_flag != NULL &&
        (g_ascii_strcasecmp(dev_mode_flag, "1") == 0 ||
         g_ascii_strcasecmp(dev_mode_flag, "true") == 0 ||
         g_ascii_strcasecmp(dev_mode_flag, "yes") == 0 ||
         g_ascii_strcasecmp(dev_mode_flag, "on") == 0)) {
        return TRUE;
    }

    if (expected_password == NULL || expected_password[0] == '\0') {
        expected_password = "wmtool-liara-dev";
    }

    for (i = 1; i < argc; i++) {
        if (g_strcmp0(argv[i], "--dev") == 0 || g_strcmp0(argv[i], "dev") == 0) {
            if ((i + 1) >= argc) {
                return TRUE;
            }

            if (g_strcmp0(argv[i + 1], expected_password) == 0) {
                return TRUE;
            }

            g_printerr("Dev mode password rejected.\n");
            return FALSE;
        }
    }

    return FALSE;
}

static void
load_css(void)
{
    GtkCssProvider *provider = gtk_css_provider_new();
    g_autofree char *exe_dir = g_path_get_dirname(g_get_prgname());
    g_autofree char *parent_dir = NULL;
    g_autofree char *candidate_builddir = NULL;
    g_autofree char *candidate_dist = NULL;
    g_autofree char *candidate_parent_dist = NULL;
    g_autofree char *css_path = NULL;
    GFile *file;
    GError *error = NULL;

    if (g_path_is_absolute(g_get_prgname())) {
        parent_dir = g_path_get_dirname(exe_dir);
        candidate_builddir = g_build_filename(exe_dir, "style.css", NULL);
        candidate_dist = g_build_filename(parent_dir, "config", "style.css", NULL);
        candidate_parent_dist = g_build_filename(parent_dir, "dist", "config", "style.css", NULL);

        if (g_file_test(candidate_builddir, G_FILE_TEST_EXISTS)) {
            css_path = g_strdup(candidate_builddir);
        } else if (g_file_test(candidate_dist, G_FILE_TEST_EXISTS)) {
            css_path = g_strdup(candidate_dist);
        } else if (g_file_test(candidate_parent_dist, G_FILE_TEST_EXISTS)) {
            css_path = g_strdup(candidate_parent_dist);
        }
    } else {
        g_autofree char *cwd = g_get_current_dir();
        css_path = g_build_filename(cwd, "style.css", NULL);
    }

    if (css_path == NULL) {
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
    LiaraLaunchConfig *config = user_data;
    GtkWidget *window = liara_window_new(app, config != NULL ? config->dev_mode : FALSE);
    load_css();
    gtk_window_present(GTK_WINDOW(window));
}

int
main(int argc, char **argv)
{
    const char *app_id = NULL;
    GtkApplication *app;
    g_autofree char *program_path = NULL;
    LiaraLaunchConfig config = {0};
    int status;

    g_setenv("GTK_USE_PORTAL", "0", TRUE);
    config.dev_mode = is_dev_mode_enabled(argc, argv);
    app_id = config.dev_mode ? "ai.liara.gtkui.dev" : "ai.liara.gtkui";
    app = gtk_application_new(app_id, G_APPLICATION_DEFAULT_FLAGS);
    if (argc > 0 && argv[0] != NULL) {
        if (g_path_is_absolute(argv[0])) {
            program_path = g_strdup(argv[0]);
        } else {
            g_autofree char *cwd = g_get_current_dir();
            program_path = g_canonicalize_filename(argv[0], cwd);
        }
        g_set_prgname(program_path);
    }
    g_signal_connect(app, "activate", G_CALLBACK(on_activate), &config);
    status = g_application_run(G_APPLICATION(app), argc, argv);
    g_object_unref(app);
    return status;
}
