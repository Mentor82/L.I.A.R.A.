#include "server_manager_internal.h"

gchar *
build_default_env_file_path(const gchar *project_root)
{
    if (project_root == NULL || project_root[0] == '\0') {
        return NULL;
    }

    return g_build_filename(project_root, ".env", NULL);
}

gchar *
build_default_log_file_path(void)
{
    const gchar *configured = g_getenv("LIARA_SERVER_MANAGER_LOG");

    if (configured != NULL && configured[0] != '\0') {
        return g_strdup(configured);
    }

    g_autofree gchar *cwd = g_get_current_dir();
    return g_build_filename(cwd, "logs", "ui", "server-manager.log", NULL);
}

void
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

gchar *
build_server_manager_config_path(void)
{
    const gchar *configured = g_getenv("LIARA_SERVER_MANAGER_CONFIG");

    if (configured != NULL && configured[0] != '\0') {
        return g_strdup(configured);
    }

    g_autofree gchar *cwd = g_get_current_dir();
    return g_build_filename(cwd, "config", "server-manager.json", NULL);
}

void
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

void
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

gchar *
trimmed_copy(const gchar *value)
{
    g_autofree gchar *copy = g_strdup(value != NULL ? value : "");
    return g_strdup(g_strstrip(copy));
}

gchar *
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

void
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
