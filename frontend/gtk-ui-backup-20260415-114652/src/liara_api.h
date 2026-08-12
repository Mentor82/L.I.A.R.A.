#ifndef LIARA_API_H
#define LIARA_API_H

#include <gtk/gtk.h>
#include <libsoup/soup.h>

typedef struct _LiaraApi LiaraApi;

typedef void (*LiaraApiCallback)(const char *response_text, GError *error, gpointer user_data);
typedef void (*LiaraApiStreamChunkCallback)(const char *chunk_text, gpointer user_data);
typedef void (*LiaraApiStreamHeartbeatCallback)(const char *heartbeat_payload, gpointer user_data);
typedef void (*LiaraApiStreamCompleteCallback)(const char *final_payload, GError *error, gpointer user_data);

LiaraApi *liara_api_new(const char *base_url);
void liara_api_free(LiaraApi *api);
const char *liara_api_get_base_url(LiaraApi *api);
void liara_api_set_base_url(LiaraApi *api, const char *base_url);

void liara_api_get_health(LiaraApi *api, LiaraApiCallback callback, gpointer user_data);
void liara_api_get_health_backends(LiaraApi *api, LiaraApiCallback callback, gpointer user_data);
void liara_api_get_session(
    LiaraApi *api,
    const char *session_id,
    const char *user_id,
    LiaraApiCallback callback,
    gpointer user_data
);
void liara_api_post_session(
    LiaraApi *api,
    const char *session_id,
    const char *user_id,
    const char *sandbox_root,
    LiaraApiCallback callback,
    gpointer user_data
);
void liara_api_post_chat(
    LiaraApi *api,
    const char *session_id,
    const char *user_id,
    const char *message,
    int max_tokens,
    LiaraApiCallback callback,
    gpointer user_data
);
void liara_api_post_chat_stream(
    LiaraApi *api,
    const char *session_id,
    const char *user_id,
    const char *message,
    int max_tokens,
    LiaraApiStreamChunkCallback chunk_callback,
    LiaraApiStreamHeartbeatCallback heartbeat_callback,
    LiaraApiStreamCompleteCallback complete_callback,
    gpointer user_data
);
void liara_api_get_history(
    LiaraApi *api,
    const char *session_id,
    int limit,
    gboolean include_tool_messages,
    LiaraApiCallback callback,
    gpointer user_data
);
void liara_api_get_tools(LiaraApi *api, LiaraApiCallback callback, gpointer user_data);
void liara_api_post_tool_invoke(
    LiaraApi *api,
    const char *tool_name,
    const char *parameters_json,
    int timeout_seconds,
    LiaraApiCallback callback,
    gpointer user_data
);

#endif
