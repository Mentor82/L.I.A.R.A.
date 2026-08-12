#pragma once

#include "embedding_config.hpp"

#include <chrono>
#include <cstddef>
#include <mutex>
#include <string>
#include <vector>

namespace liara::embedding {

struct EmbeddingResult {
    std::vector<float> vector;
    std::string model;
    std::string device;
    std::string runtime_backend = "openvino-cpp";
    std::size_t dimensions = 0;
    double infer_ms = 0.0;
    bool normalized = true;
};

class EmbeddingEngine {
public:
    explicit EmbeddingEngine(EmbeddingConfig config);

    bool load();
    EmbeddingResult embed(const std::string& text, bool normalize);

    bool ready() const;
    bool degraded() const;
    const std::string& error() const;
    const EmbeddingConfig& config() const;
    const std::string& effective_device() const;
    const std::string& model_source() const;
    std::size_t effective_dims() const;
    long long warm_age_ms() const;

private:
    bool load_on_device(const std::string& device, std::string& error);
    void validate_dimensions(std::size_t dims);

    EmbeddingConfig config_;
    mutable std::mutex mutex_;
    bool ready_ = false;
    bool degraded_ = false;
    std::string error_;
    std::string effective_device_;
    std::string model_source_;
    std::size_t effective_dims_ = 0;
    std::chrono::steady_clock::time_point ready_since_{};

    struct Impl;
    Impl* impl_ = nullptr;
};

} // namespace liara::embedding
