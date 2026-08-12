#include "embedding_engine.hpp"

#include <openvino/openvino.hpp>

#include <algorithm>
#include <cmath>
#include <cstring>
#include <filesystem>
#include <map>
#include <memory>
#include <numeric>
#include <stdexcept>
#include <unordered_map>

namespace liara::embedding {

namespace fs = std::filesystem;

struct EmbeddingEngine::Impl {
    ov::Core core;
    ov::CompiledModel tokenizer;
    ov::CompiledModel model;
    bool tokenizer_ready = false;
    std::string tokenizer_input_name;
};

namespace {

std::string resolve_model_xml(const std::string& model_path) {
    fs::path path(model_path);
    if (fs::is_directory(path)) {
        const fs::path candidate = path / "openvino_model.xml";
        if (fs::exists(candidate)) {
            return candidate.string();
        }
    }
    return model_path;
}

std::string resolve_tokenizer_xml(const std::string& model_path) {
    fs::path path(model_path);
    if (fs::is_regular_file(path)) {
        path = path.parent_path();
    }
    const fs::path candidate = path / "openvino_tokenizer.xml";
    if (fs::exists(candidate)) {
        return candidate.string();
    }
    return {};
}

ov::AnyMap build_compile_properties(const std::string& device) {
    ov::AnyMap properties;
    properties.emplace(ov::hint::performance_mode.name(), ov::hint::PerformanceMode::LATENCY);
    properties.emplace(ov::hint::num_requests.name(), 1);
    if (device == "CPU") {
        properties.emplace(ov::inference_num_threads.name(), 2);
    }
    return properties;
}

std::map<std::string, ov::PartialShape> build_static_reshape(
    const std::shared_ptr<ov::Model>& model,
    std::size_t max_seq_len) {
    std::map<std::string, ov::PartialShape> reshape_map;
    for (const auto& input : model->inputs()) {
        const auto name = input.get_any_name();
        const auto rank = input.get_partial_shape().rank();
        if (!rank.is_static()) {
            continue;
        }
        const auto rank_len = rank.get_length();
        if (rank_len == 2) {
            reshape_map[name] = ov::PartialShape({1, static_cast<long long>(max_seq_len)});
        } else if (rank_len == 1) {
            reshape_map[name] = ov::PartialShape({1});
        }
    }
    return reshape_map;
}

std::size_t infer_output_dims(const ov::CompiledModel& model) {
    const auto output = model.output(0);
    const auto shape = output.get_partial_shape();
    if (!shape.rank().is_static()) {
        return 0;
    }
    const auto rank = shape.rank().get_length();
    if (rank < 1) {
        return 0;
    }
    const auto last = shape[static_cast<std::size_t>(rank - 1)];
    if (!last.is_static()) {
        return 0;
    }
    return static_cast<std::size_t>(last.get_length());
}

bool model_requires_tokenizer(const std::shared_ptr<ov::Model>& model) {
    for (const auto& input : model->inputs()) {
        if (input.get_element_type() != ov::element::string) {
            return true;
        }
    }
    return false;
}

std::size_t last_token_index_from_mask(const ov::Tensor* attention_mask, std::size_t fallback_seq_len) {
    if (attention_mask == nullptr || attention_mask->get_size() == 0) {
        return fallback_seq_len > 0 ? fallback_seq_len - 1 : 0;
    }

    const auto shape = attention_mask->get_shape();
    if (shape.size() < 2 || shape[1] == 0) {
        return fallback_seq_len > 0 ? fallback_seq_len - 1 : 0;
    }
    const auto seq_len = shape[1];
    std::size_t last = 0;

    const auto type = attention_mask->get_element_type();
    if (type == ov::element::i64) {
        const auto* data = attention_mask->data<const std::int64_t>();
        for (std::size_t i = 0; i < seq_len; ++i) {
            if (data[i] != 0) {
                last = i;
            }
        }
    } else if (type == ov::element::i32) {
        const auto* data = attention_mask->data<const std::int32_t>();
        for (std::size_t i = 0; i < seq_len; ++i) {
            if (data[i] != 0) {
                last = i;
            }
        }
    } else {
        return fallback_seq_len > 0 ? fallback_seq_len - 1 : 0;
    }
    return last;
}

std::vector<float> tensor_to_vector(
    const ov::Tensor& tensor,
    const ov::Tensor* attention_mask,
    std::size_t expected_dims) {
    const auto shape = tensor.get_shape();
    const auto total = tensor.get_size();
    if (total == 0) {
        throw std::runtime_error("embedding output tensor is empty");
    }

    std::vector<float> all(total);
    const auto type = tensor.get_element_type();
    if (type == ov::element::f32) {
        const auto* data = tensor.data<const float>();
        std::copy(data, data + total, all.begin());
    } else if (type == ov::element::f16) {
        const auto* data = tensor.data<const ov::float16>();
        std::transform(data, data + total, all.begin(), [](ov::float16 value) {
            return static_cast<float>(value);
        });
    } else {
        throw std::runtime_error("unsupported embedding output element type: " + type.to_string());
    }

    if (shape.size() == 2) {
        const auto dims = shape[1];
        return std::vector<float>(all.begin(), all.begin() + static_cast<std::ptrdiff_t>(dims));
    }

    if (shape.size() == 3) {
        const auto seq_len = shape[1];
        const auto dims = shape[2];
        const auto selected_dims = expected_dims > 0 ? expected_dims : dims;
        if (selected_dims > dims) {
            throw std::runtime_error("configured dims exceed output hidden size");
        }
        const auto token_index = std::min(last_token_index_from_mask(attention_mask, seq_len), seq_len - 1);
        const auto offset = token_index * dims;
        return std::vector<float>(
            all.begin() + static_cast<std::ptrdiff_t>(offset),
            all.begin() + static_cast<std::ptrdiff_t>(offset + selected_dims));
    }

    if (expected_dims > 0 && all.size() >= expected_dims) {
        return std::vector<float>(all.begin(), all.begin() + static_cast<std::ptrdiff_t>(expected_dims));
    }
    return all;
}

ov::Tensor build_string_tensor(const std::string& text) {
    ov::Tensor tensor(ov::element::string, ov::Shape{1});
    tensor.data<std::string>()[0] = text;
    return tensor;
}

// Pad or truncate a 2D int tensor [1, actual_len] to [1, target_len].
// Padding value: 0 for attention_mask is correct (ignore), 0 for input_ids (padding token).
ov::Tensor pad_or_truncate_1d(const ov::Tensor& src, std::size_t target_len) {
    const auto shape = src.get_shape();
    if (shape.size() != 2 || shape[0] != 1) {
        return src; // can't handle, pass through
    }
    const auto actual_len = shape[1];
    if (actual_len == target_len) {
        return src;
    }
    const auto elem_type = src.get_element_type();
    ov::Tensor dst(elem_type, ov::Shape{1, target_len});
    const auto copy_len = std::min(actual_len, target_len);

    if (elem_type == ov::element::i64) {
        auto* d = dst.data<std::int64_t>();
        const auto* s = src.data<const std::int64_t>();
        std::memset(d, 0, target_len * sizeof(std::int64_t));
        std::copy(s, s + copy_len, d);
    } else if (elem_type == ov::element::i32) {
        auto* d = dst.data<std::int32_t>();
        const auto* s = src.data<const std::int32_t>();
        std::memset(d, 0, target_len * sizeof(std::int32_t));
        std::copy(s, s + copy_len, d);
    } else {
        return src; // unknown type, pass through
    }
    return dst;
}

void normalize_l2(std::vector<float>& vector) {
    double sum = 0.0;
    for (const auto value : vector) {
        sum += static_cast<double>(value) * static_cast<double>(value);
    }
    if (sum <= 0.0) {
        return;
    }
    const auto norm = static_cast<float>(std::sqrt(sum));
    for (auto& value : vector) {
        value /= norm;
    }
}

} // namespace

EmbeddingEngine::EmbeddingEngine(EmbeddingConfig config)
    : config_(std::move(config)), impl_(new Impl()) {}

bool EmbeddingEngine::load() {
    std::lock_guard<std::mutex> lock(mutex_);
    ready_ = false;
    degraded_ = false;
    error_.clear();
    effective_device_.clear();
    effective_dims_ = 0;

    std::string load_error;
    if (load_on_device(config_.device, load_error)) {
        ready_ = true;
        ready_since_ = std::chrono::steady_clock::now();
        return true;
    }

    if (config_.allow_cpu_fallback && config_.device != "CPU") {
        std::string cpu_error;
        if (load_on_device("CPU", cpu_error)) {
            ready_ = true;
            degraded_ = true;
            error_ = "primary_device_error: " + load_error;
            ready_since_ = std::chrono::steady_clock::now();
            return true;
        }
        error_ = "primary_device_error: " + load_error + "; cpu_fallback_error: " + cpu_error;
        return false;
    }

    error_ = load_error;
    return false;
}

bool EmbeddingEngine::load_on_device(const std::string& device, std::string& error) {
    try {
        model_source_ = resolve_model_xml(config_.model_path);
        if (!config_.cache_dir.empty()) {
            fs::create_directories(config_.cache_dir);
            impl_->core.set_property({{"CACHE_DIR", config_.cache_dir}});
        }

        if (!config_.tokenizer_extension_dll.empty()) {
            try {
                impl_->core.add_extension(config_.tokenizer_extension_dll);
            } catch (const std::exception&) {
                // Extension already loaded or not found; continue
            }
        }

        const auto tokenizer_xml = resolve_tokenizer_xml(config_.model_path);
        impl_->tokenizer_ready = false;
        impl_->tokenizer_input_name.clear();
        if (!tokenizer_xml.empty()) {
            auto tokenizer_model = impl_->core.read_model(tokenizer_xml);
            impl_->tokenizer = impl_->core.compile_model(tokenizer_model, "CPU");
            impl_->tokenizer_input_name = impl_->tokenizer.input(0).get_any_name();
            impl_->tokenizer_ready = true;
        }

        auto model = impl_->core.read_model(model_source_);
        if (!impl_->tokenizer_ready && model_requires_tokenizer(model)) {
            throw std::runtime_error(
                "openvino_tokenizer.xml is required next to the embedding model; "
                "refusing zero-filled token tensors for semantic embeddings");
        }

        auto reshape_map = build_static_reshape(model, config_.max_seq_len);
        if (!reshape_map.empty()) {
            model->reshape(reshape_map);
        }

        impl_->model = impl_->core.compile_model(model, device, build_compile_properties(device));
        effective_device_ = device;
        effective_dims_ = infer_output_dims(impl_->model);
        if (config_.dims > 0) {
            validate_dimensions(config_.dims);
            effective_dims_ = config_.dims;
        }
        if (effective_dims_ == 0) {
            throw std::runtime_error("could not infer embedding dimensions; set model.dims in config");
        }

        if (config_.startup_probe) {
            auto request = impl_->model.create_infer_request();
            std::vector<ov::Tensor> tensors;
            for (const auto& input : impl_->model.inputs()) {
                if (input.get_element_type() == ov::element::string) {
                    tensors.push_back(build_string_tensor("embedding startup probe"));
                } else {
                    ov::Tensor tensor(input.get_element_type(), input.get_shape());
                    std::memset(tensor.data(), 0, tensor.get_byte_size());
                    tensors.push_back(tensor);
                }
                request.set_tensor(input, tensors.back());
            }
            request.infer();
        }
        return true;
    } catch (const std::exception& ex) {
        error = ex.what();
        return false;
    }
}

void EmbeddingEngine::validate_dimensions(std::size_t dims) {
    if (effective_dims_ > 0 && effective_dims_ != dims) {
        throw std::runtime_error(
            "embedding_dimension_mismatch: config dims=" + std::to_string(dims) +
            " model dims=" + std::to_string(effective_dims_));
    }
}

EmbeddingResult EmbeddingEngine::embed(const std::string& text, bool normalize) {
    if (text.empty()) {
        throw std::runtime_error("input_text is required");
    }

    std::lock_guard<std::mutex> lock(mutex_);
    if (!ready_) {
        throw std::runtime_error(error_.empty() ? "embedding engine is not ready" : error_);
    }

    const auto start = std::chrono::steady_clock::now();
    auto request = impl_->model.create_infer_request();
    std::vector<ov::Tensor> tensors;
    tensors.reserve(impl_->model.inputs().size());
    std::unordered_map<std::string, ov::Tensor> tokenized;

    if (impl_->tokenizer_ready) {
        auto tokenizer_request = impl_->tokenizer.create_infer_request();
        auto input_tensor = build_string_tensor(text);
        tokenizer_request.set_tensor(impl_->tokenizer_input_name, input_tensor);
        tokenizer_request.infer();

        for (const auto& output : impl_->tokenizer.outputs()) {
            auto tok_tensor = tokenizer_request.get_tensor(output);
            tokenized.emplace(output.get_any_name(),
                pad_or_truncate_1d(tok_tensor, config_.max_seq_len));
        }
    }

    for (const auto& input : impl_->model.inputs()) {
        const auto name = input.get_any_name();
        auto tokenized_it = tokenized.find(name);
        if (tokenized_it != tokenized.end()) {
            request.set_tensor(input, tokenized_it->second);
            continue;
        }

        if (!impl_->tokenizer_ready && input.get_element_type() == ov::element::string) {
            tensors.push_back(build_string_tensor(text));
            request.set_tensor(input, tensors.back());
            continue;
        }

        if (!impl_->tokenizer_ready) {
            throw std::runtime_error("embedding tokenizer is not loaded");
        }

        ov::Tensor tensor(input.get_element_type(), input.get_shape());
        std::memset(tensor.data(), 0, tensor.get_byte_size());
        tensors.push_back(tensor);
        request.set_tensor(input, tensors.back());
    }

    request.infer();
    const ov::Tensor* attention_mask = nullptr;
    auto mask_it = tokenized.find("attention_mask");
    if (mask_it != tokenized.end()) {
        attention_mask = &mask_it->second;
    }
    auto vector = tensor_to_vector(request.get_output_tensor(0), attention_mask, effective_dims_);
    if (config_.dims > 0 && vector.size() != config_.dims) {
        throw std::runtime_error(
            "embedding_dimension_mismatch: vector dims=" + std::to_string(vector.size()) +
            " config dims=" + std::to_string(config_.dims));
    }
    if (normalize) {
        normalize_l2(vector);
    }

    const auto end = std::chrono::steady_clock::now();
    EmbeddingResult result;
    result.vector = std::move(vector);
    result.model = model_source_;
    result.device = effective_device_;
    result.dimensions = result.vector.size();
    result.infer_ms = std::chrono::duration<double, std::milli>(end - start).count();
    result.normalized = normalize;
    return result;
}

bool EmbeddingEngine::ready() const { return ready_; }
bool EmbeddingEngine::degraded() const { return degraded_; }
const std::string& EmbeddingEngine::error() const { return error_; }
const EmbeddingConfig& EmbeddingEngine::config() const { return config_; }
const std::string& EmbeddingEngine::effective_device() const { return effective_device_; }
const std::string& EmbeddingEngine::model_source() const { return model_source_; }
std::size_t EmbeddingEngine::effective_dims() const { return effective_dims_; }

long long EmbeddingEngine::warm_age_ms() const {
    if (!ready_) {
        return 0;
    }
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - ready_since_).count();
}

} // namespace liara::embedding
