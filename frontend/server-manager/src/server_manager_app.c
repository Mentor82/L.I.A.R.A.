#include "server_manager_internal.h"

int
liara_server_manager_run(int argc, char **argv)
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
