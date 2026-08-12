#pragma once

#include <cstddef>
#include <string>

namespace liara::embedding {

struct EmbeddingConfig {
    std::string host = "127.0.0.1";
    int port = 8030;

    std::string model_path = "C:/ai/models/OpenVINO/Qwen3-Embedding-0.6B-fp16-ov";
    std::string device = "NPU";
    std::size_t max_seq_len = 512;
    std::size_t dims = 0;
    bool normalize_default = true;

    std::string cache_dir = "C:/ai/cache/openvino";
    bool startup_probe = true;
    bool allow_cpu_fallback = true;
    std::string tokenizer_extension_dll;

    bool linep_enabled = false;
    std::string linep_heartbeat_host = "127.0.0.1";
    int linep_heartbeat_port = 8768;
    int linep_tcp_port = 8767;
    int linep_heartbeat_interval_ms = 1000;
    unsigned int linep_worker_id = 30;
    unsigned int linep_slot_id = 0;
};

EmbeddingConfig load_embedding_config(const std::string& path);

} // namespace liara::embedding
