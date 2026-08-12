#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "Ws2_32.lib")

#include "embedding_config.hpp"
#include "embedding_engine.hpp"
#include "embedding_linep.hpp"
#include "nlohmann/json.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <thread>

using json = nlohmann::json;

namespace {

std::unique_ptr<liara::embedding::EmbeddingEngine> g_engine;
std::unique_ptr<liara::embedding::LinepEmbeddingEndpoint> g_linep;
std::atomic<bool> g_running{true};
auto g_started = std::chrono::steady_clock::now();

void send_response(SOCKET sock, int status_code, const std::string& body) {
    std::string status_msg = (status_code == 200) ? "OK"
                           : (status_code == 400) ? "Bad Request"
                           : (status_code == 404) ? "Not Found"
                           : (status_code == 500) ? "Internal Server Error"
                           : (status_code == 503) ? "Service Unavailable"
                           : "Unknown";

    std::ostringstream resp;
    resp << "HTTP/1.1 " << status_code << " " << status_msg << "\r\n"
         << "Content-Type: application/json\r\n"
         << "Content-Length: " << body.size() << "\r\n"
         << "Connection: close\r\n"
         << "\r\n"
         << body;

    const auto raw = resp.str();
    const char* cursor = raw.c_str();
    int remaining = static_cast<int>(raw.size());
    while (remaining > 0) {
        const int sent = send(sock, cursor, remaining, 0);
        if (sent <= 0) {
            break;
        }
        cursor += sent;
        remaining -= sent;
    }
}

void send_json(SOCKET sock, int status_code, const json& payload) {
    send_response(sock, status_code, payload.dump());
}

struct HttpRequest {
    std::string method;
    std::string path;
    std::string body;
};

HttpRequest parse_request(const std::string& raw) {
    HttpRequest request;
    std::istringstream stream(raw);
    stream >> request.method >> request.path;
    const auto header_end = raw.find("\r\n\r\n");
    if (header_end != std::string::npos) {
        request.body = raw.substr(header_end + 4);
    }
    return request;
}

std::string read_http_request(SOCKET client) {
    std::string raw;
    char buffer[4096];
    std::size_t content_length = 0;
    bool headers_done = false;

    while (true) {
        const int n = recv(client, buffer, sizeof(buffer), 0);
        if (n <= 0) {
            break;
        }
        raw.append(buffer, n);

        const auto header_end = raw.find("\r\n\r\n");
        if (!headers_done && header_end != std::string::npos) {
            headers_done = true;
            auto cl_pos = raw.find("Content-Length:");
            if (cl_pos == std::string::npos) {
                cl_pos = raw.find("content-length:");
            }
            if (cl_pos != std::string::npos) {
                const auto line_end = raw.find("\r\n", cl_pos);
                content_length = static_cast<std::size_t>(
                    std::stoul(raw.substr(cl_pos + 15, line_end - cl_pos - 15)));
            }
        }

        if (headers_done) {
            const auto body_received = raw.size() - (header_end + 4);
            if (body_received >= content_length) {
                break;
            }
        }
    }
    return raw;
}

json health_payload() {
    const auto uptime_s = std::chrono::duration<double>(
        std::chrono::steady_clock::now() - g_started).count();
    const bool ready = g_engine && g_engine->ready();
    const auto& cfg = g_engine->config();
    const auto runtime_status = ready ? (g_engine->degraded() ? "partial" : "success") : "failed";
    const auto backend_health = ready ? (g_engine->degraded() ? "degraded" : "healthy") : "unavailable";
    const auto model = g_engine->model_source().empty() ? cfg.model_path : g_engine->model_source();
    return {
        {"status", {
            {"status", runtime_status},
            {"backend", "embedding"},
            {"degraded", !ready || g_engine->degraded()},
            {"error", (!ready || g_engine->degraded()) ? json(g_engine->error()) : json(nullptr)},
            {"metadata", {
                {"runtime_backend", "openvino-cpp"},
                {"device", g_engine->effective_device().empty() ? cfg.device : g_engine->effective_device()},
                {"configured_device", cfg.device},
                {"model", model},
                {"configured_model_id", cfg.model_path},
                {"configured_model_dir", cfg.model_path},
                {"cache", {
                    {"enabled", false},
                    {"items", 0},
                    {"max_items", 0},
                    {"ttl_seconds", 0}
                }},
                {"runtime_stats", {
                    {"request_count", 0},
                    {"failed_count", 0},
                    {"failure_rate", 0.0},
                    {"cache_hit_count", 0},
                    {"cache_hit_rate", 0.0},
                    {"degraded_request_count", 0},
                    {"fallback_rate", 0.0},
                    {"truncation_count", 0},
                    {"truncation_rate", 0.0},
                    {"runtime_backend_switch_count", 0},
                    {"last_runtime_backend", "openvino-cpp"},
                    {"avg_latency_ms", 0.0},
                    {"max_latency_ms", 0.0}
                }},
                {"alerts", {
                    {"active", json::array()},
                    {"thresholds", json::object()}
                }}
            }}
        }},
        {"backend_health", {{"embedding", backend_health}}},
        {"device", g_engine->effective_device().empty() ? cfg.device : g_engine->effective_device()},
        {"execution_devices", json::array({g_engine->effective_device().empty() ? cfg.device : g_engine->effective_device()})},
        {"model", model},
        {"dimensions", g_engine->effective_dims() > 0 ? g_engine->effective_dims() : cfg.dims},
        {"runtime_backend", "openvino-cpp"},
        {"effective_max_length", cfg.max_seq_len},
        {"configured_model_id", cfg.model_path},
        {"configured_model_dir", cfg.model_path},
        {"ready", ready},
        {"degraded", g_engine->degraded()},
        {"error", g_engine->error()},
        {"runtime", "openvino-cpp"},
        {"device", g_engine->effective_device().empty() ? cfg.device : g_engine->effective_device()},
        {"configured_device", cfg.device},
        {"model", model},
        {"configured_model", cfg.model_path},
        {"dims", g_engine->effective_dims()},
        {"configured_dims", cfg.dims},
        {"max_seq_len", cfg.max_seq_len},
        {"normalize_default", cfg.normalize_default},
        {"warm_age_ms", g_engine->warm_age_ms()},
        {"linep", {
            {"enabled", cfg.linep_enabled},
            {"status", g_linep ? g_linep->status() : (cfg.linep_enabled ? "not_started" : "disabled")},
            {"running", g_linep ? g_linep->running() : false},
            {"heartbeat_host", cfg.linep_heartbeat_host},
            {"heartbeat_port", cfg.linep_heartbeat_port},
            {"tcp_port", cfg.linep_tcp_port},
            {"worker_id", cfg.linep_worker_id},
            {"slot_id", cfg.linep_slot_id}
        }},
        {"uptime_s", std::round(uptime_s * 10.0) / 10.0}
    };
}

void handle_embedding(SOCKET client, const HttpRequest& request) {
    if (!g_engine || !g_engine->ready()) {
        send_json(client, 503, {
            {"item", nullptr},
            {"status", {
                {"status", "failed"},
                {"backend", "embedding"},
                {"degraded", true},
                {"error", g_engine ? g_engine->error() : "engine_not_initialized"}
            }}
        });
        return;
    }

    json body;
    try {
        body = json::parse(request.body);
    } catch (...) {
        send_json(client, 400, {
            {"item", nullptr},
            {"status", {
                {"status", "failed"},
                {"backend", "embedding"},
                {"degraded", true},
                {"error", "invalid_json_body"}
            }}
        });
        return;
    }

    const std::string input_text = body.contains("input_text") && body["input_text"].is_string()
        ? body["input_text"].get<std::string>()
        : "";
    const std::string default_model =
        g_engine->model_source().empty() ? g_engine->config().model_path : g_engine->model_source();
    const std::string requested_model = body.contains("model") && body["model"].is_string()
        ? body["model"].get<std::string>()
        : default_model;
    const bool normalize = body.contains("normalize") && body["normalize"].is_boolean()
        ? body["normalize"].get<bool>()
        : g_engine->config().normalize_default;
    const json metadata = body.contains("metadata") && body["metadata"].is_object()
        ? body["metadata"]
        : json::object();

    try {
        auto result = g_engine->embed(input_text, normalize);
        json response_metadata = metadata;
        response_metadata["runtime"] = result.runtime_backend;
        response_metadata["device"] = result.device;
        response_metadata["normalized"] = result.normalized;
        response_metadata["embedding_latency_ms"] = std::round(result.infer_ms * 1000.0) / 1000.0;
        response_metadata["configured_dims"] = g_engine->config().dims;
        response_metadata["max_seq_len"] = g_engine->config().max_seq_len;

        send_json(client, 200, {
            {"item", {
                {"model", requested_model},
                {"dimensions", result.dimensions},
                {"vector", result.vector},
                {"metadata", response_metadata}
            }},
            {"status", {
                {"status", g_engine->degraded() ? "partial" : "success"},
                {"backend", "embedding"},
                {"degraded", g_engine->degraded()},
                {"error", g_engine->degraded() ? json(g_engine->error()) : json(nullptr)},
                {"metadata", {
                    {"runtime", result.runtime_backend},
                    {"device", result.device},
                    {"embedding_latency_ms", response_metadata["embedding_latency_ms"]}
                }}
            }}
        });
    } catch (const std::exception& ex) {
        send_json(client, 500, {
            {"item", nullptr},
            {"status", {
                {"status", "failed"},
                {"backend", "embedding"},
                {"degraded", true},
                {"error", ex.what()}
            }}
        });
    }
}

void handle_client(SOCKET client) {
    try {
        const auto raw = read_http_request(client);
        const auto request = parse_request(raw);

        if (request.method == "GET" && request.path == "/health") {
            const bool ready = g_engine && g_engine->ready();
            send_json(client, ready ? 200 : 503, health_payload());
        } else if (request.method == "POST" && request.path == "/embedding/generate") {
            handle_embedding(client, request);
        } else {
            send_json(client, 404, {{"status", "not_found"}});
        }
    } catch (const std::exception& ex) {
        send_json(client, 500, {
            {"item", nullptr},
            {"status", {
                {"status", "failed"},
                {"backend", "embedding"},
                {"degraded", true},
                {"error", ex.what()}
            }}
        });
    } catch (...) {
        send_json(client, 500, {
            {"item", nullptr},
            {"status", {
                {"status", "failed"},
                {"backend", "embedding"},
                {"degraded", true},
                {"error", "unknown_embedding_server_error"}
            }}
        });
    }
    closesocket(client);
}

void print_usage() {
    std::cout << "Usage: LiaraEmbeddingService.exe [--config=<path>] [--port=<port>] "
              << "[--device=NPU|CPU|GPU] [--model=<model-dir-or-xml>] [--dims=<n>] "
              << "[--max-seq-len=<n>]\n";
}

liara::embedding::EmbeddingConfig parse_args(int argc, char** argv) {
    std::string config_path;
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        constexpr const char* prefix = "--config=";
        if (arg.rfind(prefix, 0) == 0) {
            config_path = arg.substr(std::char_traits<char>::length(prefix));
        }
    }

    auto config = liara::embedding::load_embedding_config(config_path);
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        auto value_after = [&arg](const char* prefix) -> std::string {
            return arg.substr(std::char_traits<char>::length(prefix));
        };

        if (arg == "--help" || arg == "-h") {
            print_usage();
            std::exit(0);
        } else if (arg.rfind("--config=", 0) == 0) {
            continue;
        } else if (arg.rfind("--port=", 0) == 0) {
            config.port = std::stoi(value_after("--port="));
        } else if (arg.rfind("--device=", 0) == 0) {
            config.device = value_after("--device=");
        } else if (arg.rfind("--model=", 0) == 0) {
            config.model_path = value_after("--model=");
        } else if (arg.rfind("--dims=", 0) == 0) {
            config.dims = static_cast<std::size_t>(std::stoull(value_after("--dims=")));
        } else if (arg.rfind("--max-seq-len=", 0) == 0) {
            config.max_seq_len = static_cast<std::size_t>(std::stoull(value_after("--max-seq-len=")));
        } else {
            throw std::runtime_error("Unknown argument: " + arg);
        }
    }
    return config;
}

} // namespace

int main(int argc, char** argv) {
    try {
        auto config = parse_args(argc, argv);
        g_engine = std::make_unique<liara::embedding::EmbeddingEngine>(config);
        std::cout << "Loading embedding model: " << config.model_path
                  << " on " << config.device << "...\n";
        if (!g_engine->load()) {
            std::cerr << "Embedding engine failed to load: " << g_engine->error() << "\n";
        } else {
            std::cout << "Embedding engine ready. device=" << g_engine->effective_device()
                      << " dims=" << g_engine->effective_dims() << "\n";
        }

        if (config.linep_enabled) {
            g_linep = std::make_unique<liara::embedding::LinepEmbeddingEndpoint>(*g_engine, config);
            g_linep->start();
            std::cout << "LiNeP enabled. heartbeat="
                      << config.linep_heartbeat_host << ":" << config.linep_heartbeat_port
                      << " tcp=0.0.0.0:" << config.linep_tcp_port
                      << " worker=" << config.linep_worker_id
                      << " slot=" << config.linep_slot_id << "\n";
        }

        WSADATA wsa_data;
        if (WSAStartup(MAKEWORD(2, 2), &wsa_data) != 0) {
            std::cerr << "WSAStartup failed\n";
            return 1;
        }

        SOCKET server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (server == INVALID_SOCKET) {
            std::cerr << "socket() failed\n";
            WSACleanup();
            return 1;
        }

        sockaddr_in address{};
        address.sin_family = AF_INET;
        address.sin_port = htons(static_cast<u_short>(config.port));
        inet_pton(AF_INET, config.host.c_str(), &address.sin_addr);

        if (bind(server, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == SOCKET_ERROR) {
            std::cerr << "bind() failed on " << config.host << ":" << config.port << "\n";
            closesocket(server);
            WSACleanup();
            return 1;
        }
        if (listen(server, SOMAXCONN) == SOCKET_ERROR) {
            std::cerr << "listen() failed\n";
            closesocket(server);
            WSACleanup();
            return 1;
        }

        std::cout << "LiaraEmbeddingService listening on http://"
                  << config.host << ":" << config.port << "\n";

        while (g_running) {
            SOCKET client = accept(server, nullptr, nullptr);
            if (client == INVALID_SOCKET) {
                continue;
            }
            std::thread(handle_client, client).detach();
        }

        closesocket(server);
        WSACleanup();
        return 0;
    } catch (const std::exception& ex) {
        std::cerr << "Fatal: " << ex.what() << "\n";
        return 1;
    }
}
