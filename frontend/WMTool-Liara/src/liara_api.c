#include "liara_api.h"

#include <json-glib/json-glib.h>

struct _LiaraApi {
    SoupSession *session;
    char *base_url;
};

typedef struct {
    LiaraApiCallback callback;
    gpointer user_data;
} RequestContext;

typedef struct {
    LiaraApiStreamChunkCallback chunk_callback;
    LiaraApiStreamProgressCallback progress_callback;
    LiaraApiStreamHeartbeatCallback heartbeat_callback;
    LiaraApiStreamCompleteCallback complete_callback;
    gpointer user_data;
    char *payload;
    char *base_url;
} StreamRequestContext;

typedef struct {
    LiaraApiStreamChunkCallback callback;
    gpointer user_data;
    char *chunk_text;
} StreamChunkDispatch;

typedef struct {
    LiaraApiStreamProgressCallback callback;
    gpointer user_data;
    char *progress_payload;
} StreamProgressDispatch;

typedef struct {
    LiaraApiStreamCompleteCallback callback;
    gpointer user_data;
    char *final_payload;
    GError *error;
} StreamCompleteDispatch;

typedef struct {
    LiaraApiStreamHeartbeatCallback callback;
    gpointer user_data;
    char *heartbeat_payload;
} StreamHeartbeatDispatch;

static void
request_context_free(RequestContext *context)
{
    g_free(context);
}

static void
stream_request_context_free(StreamRequestContext *context)
{
    if (context == NULL) {
        return;
    }

    g_clear_pointer(&context->payload, g_free);
    g_clear_pointer(&context->base_url, g_free);
    g_free(context);
}

static gboolean
dispatch_stream_chunk_idle(gpointer user_data)
{
    StreamChunkDispatch *dispatch = user_data;
    dispatch->callback(dispatch->chunk_text, dispatch->user_data);
    g_clear_pointer(&dispatch->chunk_text, g_free);
    g_free(dispatch);
    return G_SOURCE_REMOVE;
}

static gboolean
dispatch_stream_complete_idle(gpointer user_data)
{
    StreamCompleteDispatch *dispatch = user_data;
    dispatch->callback(dispatch->final_payload, dispatch->error, dispatch->user_data);
    g_clear_pointer(&dispatch->final_payload, g_free);
    if (dispatch->error != NULL) {
        g_error_free(dispatch->error);
    }
    g_free(dispatch);
    return G_SOURCE_REMOVE;
}

static gboolean
dispatch_stream_progress_idle(gpointer user_data)
{
    StreamProgressDispatch *dispatch = user_data;
    dispatch->callback(dispatch->progress_payload, dispatch->user_data);
    g_clear_pointer(&dispatch->progress_payload, g_free);
    g_free(dispatch);
    return G_SOURCE_REMOVE;
}

static gboolean
dispatch_stream_heartbeat_idle(gpointer user_data)
{
    StreamHeartbeatDispatch *dispatch = user_data;
    g_message("[HEARTBEAT] Delivering callback on main context");
    dispatch->callback(dispatch->heartbeat_payload, dispatch->user_data);
    g_clear_pointer(&dispatch->heartbeat_payload, g_free);
    g_free(dispatch);
    return G_SOURCE_REMOVE;
}

static void
dispatch_stream_chunk(StreamRequestContext *context, const char *chunk_text)
{
    StreamChunkDispatch *dispatch;

    if (context->chunk_callback == NULL) {
        return;
    }

    dispatch = g_new0(StreamChunkDispatch, 1);
    dispatch->callback = context->chunk_callback;
    dispatch->user_data = context->user_data;
    dispatch->chunk_text = g_strdup(chunk_text != NULL ? chunk_text : "");
    g_main_context_invoke(NULL, dispatch_stream_chunk_idle, dispatch);
}

static void
dispatch_stream_progress(StreamRequestContext *context, const char *progress_payload)
{
    StreamProgressDispatch *dispatch;

    if (context->progress_callback == NULL) {
        return;
    }

    dispatch = g_new0(StreamProgressDispatch, 1);
    dispatch->callback = context->progress_callback;
    dispatch->user_data = context->user_data;
    dispatch->progress_payload = g_strdup(progress_payload != NULL ? progress_payload : "");
    g_main_context_invoke(NULL, dispatch_stream_progress_idle, dispatch);
}

static void
dispatch_stream_complete(StreamRequestContext *context, const char *final_payload, GError *error)
{
    StreamCompleteDispatch *dispatch;

    if (context->complete_callback == NULL) {
        return;
    }

    dispatch = g_new0(StreamCompleteDispatch, 1);
    dispatch->callback = context->complete_callback;
    dispatch->user_data = context->user_data;
    dispatch->final_payload = g_strdup(final_payload);
    dispatch->error = error != NULL ? g_error_copy(error) : NULL;
    g_main_context_invoke(NULL, dispatch_stream_complete_idle, dispatch);
}

static void
dispatch_stream_heartbeat(StreamRequestContext *context, const char *heartbeat_payload)
{
    StreamHeartbeatDispatch *dispatch;

    if (context->heartbeat_callback == NULL) {
        g_message("[HEARTBEAT] Heartbeat SSE received but NO CALLBACK registered");
        return;
    }

    g_message("[HEARTBEAT] SSE event recognized, dispatching to callback");

    dispatch = g_new0(StreamHeartbeatDispatch, 1);
    dispatch->callback = context->heartbeat_callback;
    dispatch->user_data = context->user_data;
    dispatch->heartbeat_payload = g_strdup(heartbeat_payload != NULL ? heartbeat_payload : "");
    g_main_context_invoke(NULL, dispatch_stream_heartbeat_idle, dispatch);
}

static char *
build_url(LiaraApi *api, const char *path)
{
    return g_strdup_printf("%s%s", api->base_url, path);
}

static char *
build_chat_body(
    const char *session_id,
    const char *user_id,
    const char *display_name,
    const char *message,
    const char *sandbox_root,
    int max_tokens)
{
    g_autoptr(JsonBuilder) builder = json_builder_new();
    g_autoptr(JsonGenerator) generator = json_generator_new();
    g_autoptr(JsonNode) root = NULL;

    json_builder_begin_object(builder);
    json_builder_set_member_name(builder, "session_id");
    json_builder_add_string_value(builder, session_id);
    json_builder_set_member_name(builder, "user_id");
    json_builder_add_string_value(builder, user_id);
    if (display_name != NULL && display_name[0] != '\0') {
        json_builder_set_member_name(builder, "display_name");
        json_builder_add_string_value(builder, display_name);
    }
    json_builder_set_member_name(builder, "message");
    json_builder_add_string_value(builder, message);
    if (sandbox_root != NULL && sandbox_root[0] != '\0') {
        json_builder_set_member_name(builder, "sandbox_root");
        json_builder_add_string_value(builder, sandbox_root);
    }
    json_builder_set_member_name(builder, "max_tokens");
    json_builder_add_int_value(builder, max_tokens);
    json_builder_end_object(builder);

    root = json_builder_get_root(builder);
    json_generator_set_root(generator, root);
    return json_generator_to_data(generator, NULL);
}

static char *
extract_chunk_text(const char *json_text)
{
    g_autoptr(JsonParser) parser = json_parser_new();
    JsonObject *object;

    if (!json_parser_load_from_data(parser, json_text, -1, NULL)) {
        return g_strdup("");
    }

    object = json_node_get_object(json_parser_get_root(parser));
    if (object == NULL || !json_object_has_member(object, "text")) {
        return g_strdup("");
    }

    return g_strdup(json_object_get_string_member(object, "text"));
}

static gpointer
stream_request_thread(gpointer user_data)
{
    StreamRequestContext *context = user_data;
    g_autoptr(SoupSession) session = soup_session_new();
    g_autofree char *url = g_strdup_printf("%s/chat/stream", context->base_url);
    g_autoptr(SoupMessage) message = soup_message_new("POST", url);
    g_autoptr(GError) error = NULL;
    g_autoptr(GInputStream) stream = NULL;
    g_autoptr(GDataInputStream) reader = NULL;
    g_autofree char *final_payload = NULL;
    char *current_event = NULL;

    soup_message_headers_append(
        soup_message_get_request_headers(message),
        "Accept",
        "text/event-stream"
    );
    soup_message_set_request_body_from_bytes(
        message,
        "application/json",
        g_bytes_new_take(g_strdup(context->payload), strlen(context->payload))
    );

    stream = soup_session_send(session, message, NULL, &error);
    if (error != NULL) {
        dispatch_stream_complete(context, NULL, error);
        stream_request_context_free(context);
        return NULL;
    }

    reader = g_data_input_stream_new(stream);

    while (TRUE) {
        gsize length = 0;
        g_autofree char *line = g_data_input_stream_read_line_utf8(reader, &length, NULL, &error);

        if (error != NULL) {
            dispatch_stream_complete(context, final_payload, error);
            g_clear_pointer(&current_event, g_free);
            stream_request_context_free(context);
            return NULL;
        }

        if (line == NULL) {
            break;
        }

        if (length == 0) {
            continue;
        }

        if (g_str_has_prefix(line, "event:")) {
            g_free(current_event);
            current_event = g_strdup(g_strstrip(line + 6));
            continue;
        }

        if (!g_str_has_prefix(line, "data:")) {
            continue;
        }

        if (g_strcmp0(current_event, "chunk") == 0) {
            g_autofree char *chunk_text = extract_chunk_text(g_strstrip(line + 5));
            dispatch_stream_chunk(context, chunk_text);
            continue;
        }

        if (g_strcmp0(current_event, "progress") == 0) {
            dispatch_stream_progress(context, g_strstrip(line + 5));
            continue;
        }

        if (g_strcmp0(current_event, "heartbeat") == 0) {
            dispatch_stream_heartbeat(context, g_strstrip(line + 5));
            continue;
        }

        if (g_strcmp0(current_event, "final") == 0) {
            g_clear_pointer(&final_payload, g_free);
            final_payload = g_strdup(g_strstrip(line + 5));
            continue;
        }

        if (g_strcmp0(current_event, "done") == 0) {
            break;
        }
    }

    dispatch_stream_complete(context, final_payload, NULL);
    g_clear_pointer(&current_event, g_free);
    stream_request_context_free(context);
    return NULL;
}

static void
on_message_complete(GObject *source_object, GAsyncResult *result, gpointer user_data)
{
    SoupSession *session = SOUP_SESSION(source_object);
    RequestContext *context = user_data;
    g_autoptr(GError) error = NULL;
    g_autoptr(GBytes) bytes = soup_session_send_and_read_finish(session, result, &error);

    if (error != NULL) {
        context->callback(NULL, g_steal_pointer(&error), context->user_data);
        request_context_free(context);
        return;
    }

    gsize size = 0;
    const char *data = g_bytes_get_data(bytes, &size);
    g_autofree char *text = g_strndup(data, size);

    context->callback(text, NULL, context->user_data);
    request_context_free(context);
}

static void
send_json_request(
    LiaraApi *api,
    const char *method,
    const char *path,
    const char *body,
    LiaraApiCallback callback,
    gpointer user_data)
{
    g_autofree char *url = build_url(api, path);
    SoupMessage *message = soup_message_new(method, url);
    RequestContext *context = g_new0(RequestContext, 1);

    context->callback = callback;
    context->user_data = user_data;

    if (body != NULL) {
        GBytes *payload = g_bytes_new(body, strlen(body));
        soup_message_set_request_body_from_bytes(
            message,
            "application/json",
            payload
        );
        g_bytes_unref(payload);
    }

    soup_session_send_and_read_async(
        api->session,
        message,
        G_PRIORITY_DEFAULT,
        NULL,
        on_message_complete,
        context
    );
    g_object_unref(message);
}

LiaraApi *
liara_api_new(const char *base_url)
{
    LiaraApi *api = g_new0(LiaraApi, 1);
    api->session = soup_session_new();
    api->base_url = g_strdup(base_url);
    return api;
}

void
liara_api_free(LiaraApi *api)
{
    if (api == NULL) {
        return;
    }
    g_clear_object(&api->session);
    g_clear_pointer(&api->base_url, g_free);
    g_free(api);
}

const char *
liara_api_get_base_url(LiaraApi *api)
{
    return api != NULL ? api->base_url : NULL;
}

void
liara_api_set_base_url(LiaraApi *api, const char *base_url)
{
    if (api == NULL || base_url == NULL || base_url[0] == '\0') {
        return;
    }

    g_clear_pointer(&api->base_url, g_free);
    api->base_url = g_strdup(base_url);
}

void
liara_api_get_health(LiaraApi *api, LiaraApiCallback callback, gpointer user_data)
{
    send_json_request(api, "GET", "/health", NULL, callback, user_data);
}

void
liara_api_get_health_backends(LiaraApi *api, LiaraApiCallback callback, gpointer user_data)
{
    send_json_request(api, "GET", "/health/backends", NULL, callback, user_data);
}

void
liara_api_get_sys_audit_summary(
    LiaraApi *api,
    int limit,
    gboolean blocked_only,
    const char *source,
    const char *risk_level,
    const char *command_family,
    LiaraApiCallback callback,
    gpointer user_data)
{
    g_autofree char *escaped_source = NULL;
    g_autofree char *escaped_risk = NULL;
    g_autofree char *escaped_family = NULL;
    g_autofree char *path = NULL;

    escaped_source = g_uri_escape_string((source != NULL && source[0] != '\0') ? source : "all", NULL, TRUE);
    escaped_risk = g_uri_escape_string((risk_level != NULL && risk_level[0] != '\0') ? risk_level : "all", NULL, TRUE);
    escaped_family = g_uri_escape_string((command_family != NULL && command_family[0] != '\0') ? command_family : "all", NULL, TRUE);

    path = g_strdup_printf(
        "/admin/sys-audit/summary?limit=%d&blocked_only=%s&source=%s&risk_level=%s&command_family=%s",
        limit,
        blocked_only ? "true" : "false",
        escaped_source,
        escaped_risk,
        escaped_family
    );

    send_json_request(api, "GET", path, NULL, callback, user_data);
}

void
liara_api_get_sys_audit_suspicious(
    LiaraApi *api,
    int limit,
    int max_items,
    gboolean blocked_only,
    const char *source,
    const char *risk_level,
    const char *command_family,
    LiaraApiCallback callback,
    gpointer user_data)
{
    g_autofree char *escaped_source = NULL;
    g_autofree char *escaped_risk = NULL;
    g_autofree char *escaped_family = NULL;
    g_autofree char *path = NULL;

    escaped_source = g_uri_escape_string((source != NULL && source[0] != '\0') ? source : "all", NULL, TRUE);
    escaped_risk = g_uri_escape_string((risk_level != NULL && risk_level[0] != '\0') ? risk_level : "all", NULL, TRUE);
    escaped_family = g_uri_escape_string((command_family != NULL && command_family[0] != '\0') ? command_family : "all", NULL, TRUE);

    path = g_strdup_printf(
        "/admin/sys-audit/suspicious?limit=%d&max_items=%d&blocked_only=%s&source=%s&risk_level=%s&command_family=%s",
        limit,
        max_items,
        blocked_only ? "true" : "false",
        escaped_source,
        escaped_risk,
        escaped_family
    );

    send_json_request(api, "GET", path, NULL, callback, user_data);
}

void
liara_api_get_sys_audit_preset(
    LiaraApi *api,
    const char *preset_name,
    int limit,
    int max_items,
    LiaraApiCallback callback,
    gpointer user_data)
{
    g_autofree char *escaped_preset = NULL;
    g_autofree char *path = NULL;
    const char *effective_preset = (preset_name != NULL && preset_name[0] != '\0') ? preset_name : "top-risk";

    escaped_preset = g_uri_escape_string(effective_preset, NULL, TRUE);
    path = g_strdup_printf(
        "/admin/sys-audit/presets/%s?limit=%d&max_items=%d",
        escaped_preset,
        limit,
        max_items
    );

    send_json_request(api, "GET", path, NULL, callback, user_data);
}

void
liara_api_get_session(
    LiaraApi *api,
    const char *session_id,
    const char *user_id,
    LiaraApiCallback callback,
    gpointer user_data)
{
    g_autofree char *escaped_session = g_uri_escape_string(session_id, NULL, TRUE);
    g_autofree char *escaped_user = g_uri_escape_string(user_id, NULL, TRUE);
    g_autofree char *path = g_strdup_printf(
        "/session?session_id=%s&user_id=%s",
        escaped_session,
        escaped_user
    );
    send_json_request(api, "GET", path, NULL, callback, user_data);
}

void
liara_api_post_session(
    LiaraApi *api,
    const char *session_id,
    const char *user_id,
    const char *sandbox_root,
    const char *metadata_json,
    LiaraApiCallback callback,
    gpointer user_data)
{
    g_autoptr(JsonBuilder) builder = json_builder_new();
    g_autoptr(JsonGenerator) generator = json_generator_new();
    g_autoptr(JsonParser) metadata_parser = NULL;
    g_autoptr(JsonNode) root = NULL;
    g_autofree char *body = NULL;

    json_builder_begin_object(builder);
    json_builder_set_member_name(builder, "session_id");
    json_builder_add_string_value(builder, session_id);
    json_builder_set_member_name(builder, "user_id");
    json_builder_add_string_value(builder, user_id);
    if (sandbox_root != NULL && sandbox_root[0] != '\0') {
        json_builder_set_member_name(builder, "sandbox_root");
        json_builder_add_string_value(builder, sandbox_root);
    }
    if (metadata_json != NULL && metadata_json[0] != '\0') {
        metadata_parser = json_parser_new();
        if (json_parser_load_from_data(metadata_parser, metadata_json, -1, NULL)) {
            JsonNode *metadata_root = json_parser_get_root(metadata_parser);
            if (metadata_root != NULL && JSON_NODE_HOLDS_OBJECT(metadata_root)) {
                json_builder_set_member_name(builder, "metadata");
                json_builder_add_value(builder, json_node_copy(metadata_root));
            }
        }
    }
    json_builder_end_object(builder);

    root = json_builder_get_root(builder);
    json_generator_set_root(generator, root);
    body = json_generator_to_data(generator, NULL);
    send_json_request(api, "POST", "/session", body, callback, user_data);
}

void
liara_api_post_chat(
    LiaraApi *api,
    const char *session_id,
    const char *user_id,
    const char *display_name,
    const char *message,
    const char *sandbox_root,
    int max_tokens,
    LiaraApiCallback callback,
    gpointer user_data)
{
    g_autofree char *body = build_chat_body(session_id, user_id, display_name, message, sandbox_root, max_tokens);

    send_json_request(api, "POST", "/chat", body, callback, user_data);
}

void
liara_api_post_chat_stream(
    LiaraApi *api,
    const char *session_id,
    const char *user_id,
    const char *display_name,
    const char *message,
    const char *sandbox_root,
    int max_tokens,
    LiaraApiStreamChunkCallback chunk_callback,
    LiaraApiStreamProgressCallback progress_callback,
    LiaraApiStreamHeartbeatCallback heartbeat_callback,
    LiaraApiStreamCompleteCallback complete_callback,
    gpointer user_data)
{
    StreamRequestContext *context = g_new0(StreamRequestContext, 1);

    context->chunk_callback = chunk_callback;
    context->progress_callback = progress_callback;
    context->heartbeat_callback = heartbeat_callback;
    context->complete_callback = complete_callback;
    context->user_data = user_data;
    context->payload = build_chat_body(session_id, user_id, display_name, message, sandbox_root, max_tokens);
    context->base_url = g_strdup(api->base_url);

    g_thread_new("liara-chat-stream", stream_request_thread, context);
}

void
liara_api_get_history(
    LiaraApi *api,
    const char *session_id,
    const char *run_id,
    int limit,
    gboolean include_tool_messages,
    LiaraApiCallback callback,
    gpointer user_data)
{
    g_autofree char *escaped_session = g_uri_escape_string(session_id, NULL, TRUE);
    g_autofree char *escaped_run_id = NULL;
    g_autofree char *path = NULL;

    if (run_id != NULL && run_id[0] != '\0') {
        escaped_run_id = g_uri_escape_string(run_id, NULL, TRUE);
        path = g_strdup_printf(
            "/history?session_id=%s&run_id=%s&limit=%d&include_tool_messages=%s",
            escaped_session,
            escaped_run_id,
            limit,
            include_tool_messages ? "true" : "false"
        );
    } else {
        path = g_strdup_printf(
            "/history?session_id=%s&limit=%d&include_tool_messages=%s",
            escaped_session,
            limit,
            include_tool_messages ? "true" : "false"
        );
    }

    send_json_request(api, "GET", path, NULL, callback, user_data);
}

void
liara_api_get_tools(LiaraApi *api, LiaraApiCallback callback, gpointer user_data)
{
    send_json_request(api, "GET", "/tools", NULL, callback, user_data);
}

void
liara_api_get_tool_metadata(
    LiaraApi *api,
    const char *tool_name,
    LiaraApiCallback callback,
    gpointer user_data)
{
    g_autofree char *escaped_tool = NULL;
    g_autofree char *path = NULL;

    if (tool_name == NULL || tool_name[0] == '\0') {
        callback("{\"status\":\"error\",\"detail\":\"Tool name is required.\"}", NULL, user_data);
        return;
    }

    escaped_tool = g_uri_escape_string(tool_name, NULL, TRUE);
    path = g_strdup_printf("/tools/%s", escaped_tool);
    send_json_request(api, "GET", path, NULL, callback, user_data);
}

void
liara_api_post_tool_invoke(
    LiaraApi *api,
    const char *tool_name,
    const char *parameters_json,
    int timeout_seconds,
    LiaraApiCallback callback,
    gpointer user_data)
{
    g_autoptr(JsonParser) parser = json_parser_new();
    g_autoptr(JsonBuilder) builder = json_builder_new();
    g_autoptr(JsonGenerator) generator = json_generator_new();
    g_autoptr(JsonNode) root = NULL;
    g_autofree char *body = NULL;
    g_autofree char *escaped_tool = g_uri_escape_string(tool_name, NULL, TRUE);
    g_autofree char *path = g_strdup_printf("/tools/%s/invoke", escaped_tool);

    json_builder_begin_object(builder);
    json_builder_set_member_name(builder, "parameters");
    if (parameters_json != NULL && strlen(parameters_json) > 0 &&
        json_parser_load_from_data(parser, parameters_json, -1, NULL)) {
        JsonNode *parameters_node = json_parser_get_root(parser);
        json_builder_add_value(builder, json_node_copy(parameters_node));
    } else {
        json_builder_begin_object(builder);
        json_builder_end_object(builder);
    }
    json_builder_set_member_name(builder, "timeout_seconds");
    json_builder_add_int_value(builder, timeout_seconds);
    json_builder_end_object(builder);

    root = json_builder_get_root(builder);
    json_generator_set_root(generator, root);
    body = json_generator_to_data(generator, NULL);

    send_json_request(api, "POST", path, body, callback, user_data);
}
