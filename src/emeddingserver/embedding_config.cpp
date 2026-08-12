#include "embedding_config.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <sstream>
#include <stdexcept>
#include <string>

namespace liara::embedding {

namespace {

std::string trim(std::string value) {
    auto not_space = [](unsigned char c) { return !std::isspace(c); };
    value.erase(value.begin(), std::find_if(value.begin(), value.end(), not_space));
    value.erase(std::find_if(value.rbegin(), value.rend(), not_space).base(), value.end());
    return value;
}

std::string strip_quotes(std::string value) {
    value = trim(value);
    if (value.size() >= 2 && ((value.front() == '"' && value.back() == '"') ||
                              (value.front() == '\'' && value.back() == '\''))) {
        return value.substr(1, value.size() - 2);
    }
    return value;
}

bool parse_bool(const std::string& value) {
    std::string lower = value;
    std::transform(lower.begin(), lower.end(), lower.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    lower = strip_quotes(trim(lower));
    return lower == "1" || lower == "true" || lower == "yes" || lower == "on";
}

std::size_t parse_size(const std::string& value) {
    const auto parsed = std::stoull(strip_quotes(value));
    return static_cast<std::size_t>(parsed);
}

int parse_int(const std::string& value) {
    return std::stoi(strip_quotes(value));
}

} // namespace

EmbeddingConfig load_embedding_config(const std::string& path) {
    EmbeddingConfig config;
    if (path.empty()) {
        return config;
    }

    std::ifstream input(path);
    if (!input) {
        throw std::runtime_error("Could not open embedding config: " + path);
    }

    std::string section;
    std::string line;
    while (std::getline(input, line)) {
        const auto comment = line.find('#');
        if (comment != std::string::npos) {
            line = line.substr(0, comment);
        }
        line = trim(line);
        if (line.empty()) {
            continue;
        }
        if (line.front() == '[' && line.back() == ']') {
            section = trim(line.substr(1, line.size() - 2));
            continue;
        }

        const auto eq = line.find('=');
        if (eq == std::string::npos) {
            continue;
        }

        const auto key = trim(line.substr(0, eq));
        const auto value = trim(line.substr(eq + 1));
        const auto fq = section.empty() ? key : section + "." + key;

        if (fq == "server.host") {
            config.host = strip_quotes(value);
        } else if (fq == "server.port") {
            config.port = parse_int(value);
        } else if (fq == "model.path") {
            config.model_path = strip_quotes(value);
        } else if (fq == "model.device") {
            config.device = strip_quotes(value);
        } else if (fq == "model.max_seq_len") {
            config.max_seq_len = parse_size(value);
        } else if (fq == "model.dims") {
            config.dims = parse_size(value);
        } else if (fq == "model.normalize_default") {
            config.normalize_default = parse_bool(value);
        } else if (fq == "runtime.cache_dir") {
            config.cache_dir = strip_quotes(value);
        } else if (fq == "runtime.startup_probe") {
            config.startup_probe = parse_bool(value);
        } else if (fq == "runtime.allow_cpu_fallback") {
            config.allow_cpu_fallback = parse_bool(value);
        } else if (fq == "runtime.tokenizer_extension_dll") {
            config.tokenizer_extension_dll = strip_quotes(value);
        } else if (fq == "linep.enabled") {
            config.linep_enabled = parse_bool(value);
        } else if (fq == "linep.heartbeat_host") {
            config.linep_heartbeat_host = strip_quotes(value);
        } else if (fq == "linep.heartbeat_port") {
            config.linep_heartbeat_port = parse_int(value);
        } else if (fq == "linep.tcp_port") {
            config.linep_tcp_port = parse_int(value);
        } else if (fq == "linep.heartbeat_interval_ms") {
            config.linep_heartbeat_interval_ms = parse_int(value);
        } else if (fq == "linep.worker_id") {
            config.linep_worker_id = static_cast<unsigned int>(parse_int(value));
        } else if (fq == "linep.slot_id") {
            config.linep_slot_id = static_cast<unsigned int>(parse_int(value));
        }
    }

    if (config.port < 1 || config.port > 65535) {
        throw std::runtime_error("server.port must be in range 1..65535");
    }
    if (config.max_seq_len < 1) {
        throw std::runtime_error("model.max_seq_len must be greater than zero");
    }
    if (config.model_path.empty()) {
        throw std::runtime_error("model.path is required");
    }
    if (config.device.empty()) {
        throw std::runtime_error("model.device is required");
    }
    if (config.linep_enabled) {
        if (config.linep_heartbeat_port < 1 || config.linep_heartbeat_port > 65535) {
            throw std::runtime_error("linep.heartbeat_port must be in range 1..65535");
        }
        if (config.linep_tcp_port < 1 || config.linep_tcp_port > 65535) {
            throw std::runtime_error("linep.tcp_port must be in range 1..65535");
        }
        if (config.linep_heartbeat_interval_ms < 50) {
            throw std::runtime_error("linep.heartbeat_interval_ms must be >= 50");
        }
        if (config.linep_worker_id > 65535U) {
            throw std::runtime_error("linep.worker_id must be <= 65535");
        }
        if (config.linep_slot_id > 255U) {
            throw std::runtime_error("linep.slot_id must be <= 255");
        }
    }

    return config;
}

} // namespace liara::embedding
