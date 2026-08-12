// Generic C++ Inference Server Template for OpenVINO genai
// Adapts helper_infer_server.cpp for different models
// Usage: Change MODEL_PATH, SYSTEM_PROMPT, task_type checks as needed

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "Ws2_32.lib")

#include <openvino/genai/llm_pipeline.hpp>
#include <openvino/runtime.hpp>

#include "nlohmann/json.hpp"

#include <algorithm>
#include <atomic>
#include <iostream>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <vector>
#include <chrono>

using json = nlohmann::json;
using Clock = std::chrono::steady_clock;

// ============================================================================
// CONFIGURATION SECTION
// ============================================================================

// Model configuration
struct ModelConfig {
    std::string model_path;      // Path to OpenVINO model directory
    std::string device;          // "NPU", "GPU", "CPU"
    int max_tokens = 256;
    float temperature = 0.7f;
};

// System prompts per task type
static const std::string SYSTEM_DEFAULT =
    "You are a helpful assistant. Respond with clear, concise answers.";

// ============================================================================
// MAIN SERVER STATE
// ============================================================================

struct {
    std::unique_ptr<ov::genai::LLMPipeline> pipeline;
    std::string model_label;
    std::mutex lock;
    bool ready = false;
} g_state;

// ============================================================================
// JSON EXTRACTION
// ============================================================================

static std::optional<json> extract_json(const std::string& text) {
    std::string t = text;
    auto ltrim = [](std::string& s) {
        s.erase(s.begin(), std::find_if(s.begin(), s.end(), 
                [](unsigned char c) { return !std::isspace(c); }));
    };
    auto rtrim = [](std::string& s) {
        s.erase(std::find_if(s.rbegin(), s.rend(), 
                [](unsigned char c) { return !std::isspace(c); }).base(), s.end());
    };
    
    ltrim(t); rtrim(t);
    
    try { return json::parse(t); } catch (...) {}
    
    size_t start = t.find('{');
    if (start == std::string::npos) {
        return std::nullopt;
    }
    
    int depth = 0;
    bool in_str = false;
    bool escape = false;
    
    for (size_t i = start; i < t.size(); ++i) {
        char c = t[i];
        if (escape) { escape = false; continue; }
        if (c == '\\' && in_str) { escape = true; continue; }
        if (c == '"') { in_str = !in_str; continue; }
        if (in_str) continue;
        
        if (c == '{') ++depth;
        else if (c == '}') {
            --depth;
            if (depth == 0) {
                std::string candidate = t.substr(start, i - start + 1);
                try { return json::parse(candidate); } catch (...) {}
                break;
            }
        }
    }
    
    return std::nullopt;
}

// ============================================================================
// HTTP HELPERS
// ============================================================================

static void send_response(SOCKET sock, int status_code, const std::string& body) {
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
    send(sock, raw.c_str(), static_cast<int>(raw.size()), 0);
}

static void send_json(SOCKET sock, int status_code, const json& payload) {
    send_response(sock, status_code, payload.dump());
}

// ============================================================================
// INFERENCE HANDLER
// ============================================================================

static void handle_infer(SOCKET client, const json& body) {
    if (!body.contains("prompt")) {
        send_json(client, 400, {
            {"status", "failed"},
            {"error", "missing 'prompt' field"}
        });
        closesocket(client);
        return;
    }
    
    std::string prompt = body["prompt"].get<std::string>();
    int max_tokens = body.value("max_tokens", 256);
    
    {
        std::lock_guard<std::mutex> lk(g_state.lock);
        if (!g_state.pipeline) {
            send_json(client, 503, {
                {"status", "failed"},
                {"error", "pipeline not ready"}
            });
            closesocket(client);
            return;
        }
        
        try {
            auto t0 = Clock::now();
            
            ov::genai::GenerationConfig cfg;
            cfg.max_new_tokens = max_tokens;
            cfg.temperature = 0.7f;
            
            auto result = g_state.pipeline->generate(prompt, cfg);
            std::string raw_content = static_cast<std::string>(result);
            
            auto t1 = Clock::now();
            double gen_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
            
            json response = {
                {"status", "success"},
                {"model", g_state.model_label},
                {"raw_content", raw_content},
                {"inference_time_ms", gen_ms}
            };
            
            send_json(client, 200, response);
        } catch (const std::exception& e) {
            send_json(client, 500, {
                {"status", "failed"},
                {"error", e.what()}
            });
        }
    }
    
    closesocket(client);
}

// ============================================================================
// MAIN
// ============================================================================

int main(int argc, char* argv[]) {
    int port = 8766;
    ModelConfig cfg{};
    cfg.device = "NPU";
    cfg.model_path = ".";  // Default: current directory
    
    // Parse command-line arguments
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--port" && i + 1 < argc) {
            port = std::stoi(argv[++i]);
        } else if (arg == "--device" && i + 1 < argc) {
            cfg.device = argv[++i];
        } else if (arg == "--model" && i + 1 < argc) {
            cfg.model_path = argv[++i];
        }
    }
    
    std::cout << "[inference-server] Starting on port " << port 
              << " device=" << cfg.device << std::endl;
    
    // Initialize OpenVINO pipeline
    try {
        g_state.pipeline = std::make_unique<ov::genai::LLMPipeline>(
            cfg.model_path,
            cfg.device
        );
        g_state.model_label = cfg.model_path;
        g_state.ready = true;
        std::cout << "[inference-server] Pipeline ready" << std::endl;
    } catch (const std::exception& e) {
        std::cerr << "[ERROR] Failed to initialize pipeline: " << e.what() << std::endl;
        return 1;
    }
    
    // Initialize Winsock
    WSADATA wsa_data;
    if (WSAStartup(MAKEWORD(2, 2), &wsa_data) != 0) {
        std::cerr << "[ERROR] WSAStartup failed" << std::endl;
        return 1;
    }
    
    // Create listening socket
    SOCKET listen_sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (listen_sock == INVALID_SOCKET) {
        std::cerr << "[ERROR] socket() failed" << std::endl;
        WSACleanup();
        return 1;
    }
    
    // Bind
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = inet_addr("127.0.0.1");
    addr.sin_port = htons(port);
    
    if (bind(listen_sock, (sockaddr*)&addr, sizeof(addr)) == SOCKET_ERROR) {
        std::cerr << "[ERROR] bind() failed" << std::endl;
        closesocket(listen_sock);
        WSACleanup();
        return 1;
    }
    
    // Listen
    if (listen(listen_sock, SOMAXCONN) == SOCKET_ERROR) {
        std::cerr << "[ERROR] listen() failed" << std::endl;
        closesocket(listen_sock);
        WSACleanup();
        return 1;
    }
    
    std::cout << "[inference-server] Listening on 127.0.0.1:" << port << std::endl;
    
    // Accept loop
    while (true) {
        sockaddr_in client_addr{};
        int client_addr_len = sizeof(client_addr);
        
        SOCKET client = accept(listen_sock, (sockaddr*)&client_addr, &client_addr_len);
        if (client == INVALID_SOCKET) continue;
        
        // Read HTTP request in a thread
        std::thread([client]() {
            char buffer[4096] = {};
            int received = recv(client, buffer, sizeof(buffer) - 1, 0);
            if (received <= 0) {
                closesocket(client);
                return;
            }
            
            // Simple HTTP parser
            std::string request(buffer);
            std::string method, path;
            size_t space1 = request.find(' ');
            size_t space2 = request.find(' ', space1 + 1);
            if (space1 != std::string::npos && space2 != std::string::npos) {
                method = request.substr(0, space1);
                path = request.substr(space1 + 1, space2 - space1 - 1);
            }
            
            if (path == "/infer" && method == "POST") {
                size_t body_start = request.find("\r\n\r\n");
                if (body_start != std::string::npos) {
                    std::string body_str = request.substr(body_start + 4);
                    try {
                        json body = json::parse(body_str);
                        handle_infer(client, body);
                    } catch (...) {
                        send_json(client, 400, {{"status", "failed"}, {"error", "invalid JSON"}});
                        closesocket(client);
                    }
                }
            } else if (path == "/health" && method == "GET") {
                send_json(client, 200, {
                    {"status", "ok"},
                    {"model", g_state.model_label}
                });
                closesocket(client);
            } else {
                send_json(client, 404, {{"error", "not found"}});
                closesocket(client);
            }
        }).detach();
    }
    
    closesocket(listen_sock);
    WSACleanup();
    return 0;
}
