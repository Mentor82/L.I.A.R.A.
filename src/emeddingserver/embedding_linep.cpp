#include "embedding_linep.hpp"

#include "linep/messages.hpp"
#include "linep/types.hpp"
#include "nlohmann/json.hpp"

#include "linep/src/core/framing.hpp"
#include "linep/src/pal/socket.hpp"
#include "linep/src/udp/heartbeat.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <exception>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <unordered_map>
#include <vector>

using json = nlohmann::json;

namespace liara::embedding {

namespace {

std::uint8_t clamp_byte(unsigned int value) {
    return static_cast<std::uint8_t>(std::min(value, 254U));
}

int recv_header(linep::pal::Socket& client, linep::Header& header) {
    return linep::pal::tcp_recv_all(
        client,
        reinterpret_cast<std::uint8_t*>(&header),
        static_cast<int>(sizeof(header)));
}

bool recv_header_extensions(linep::pal::Socket& client, const linep::Header& header) {
    if (header.header_len <= sizeof(linep::Header)) {
        return true;
    }

    const auto extension_len = static_cast<std::uint16_t>(
        header.header_len - static_cast<std::uint16_t>(sizeof(linep::Header)));
    std::vector<std::uint8_t> extensions(extension_len);
    return linep::pal::tcp_recv_all(
        client,
        extensions.data(),
        static_cast<int>(extensions.size())) == static_cast<int>(extensions.size());
}

bool send_frame(
    linep::pal::Socket& client,
    std::uint8_t msg_type,
    std::uint16_t flags,
    std::uint32_t sequence,
    std::uint32_t correlation_id,
    std::uint16_t worker_id,
    std::uint8_t slot_id,
    const std::string& payload) {
    const auto header = linep::core::make_header(
        msg_type,
        flags,
        static_cast<std::uint32_t>(payload.size()),
        sequence,
        correlation_id,
        worker_id,
        slot_id);

    if (linep::pal::tcp_send_all(
            client,
            reinterpret_cast<const std::uint8_t*>(&header),
            static_cast<int>(sizeof(header))) != static_cast<int>(sizeof(header))) {
        return false;
    }
    if (!payload.empty()) {
        return linep::pal::tcp_send_all(
            client,
            reinterpret_cast<const std::uint8_t*>(payload.data()),
            static_cast<int>(payload.size())) == static_cast<int>(payload.size());
    }
    return true;
}

float clamp01(float value) {
    if (value < 0.0F) {
        return 0.0F;
    }
    if (value > 1.0F) {
        return 1.0F;
    }
    return value;
}

float cosine_similarity(const std::vector<float>& a, const std::vector<float>& b) {
    if (a.empty() || b.empty() || a.size() != b.size()) {
        return 0.0F;
    }

    double dot = 0.0;
    double norm_a = 0.0;
    double norm_b = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) {
        const double da = static_cast<double>(a[i]);
        const double db = static_cast<double>(b[i]);
        dot += da * db;
        norm_a += da * da;
        norm_b += db * db;
    }

    if (norm_a <= std::numeric_limits<double>::epsilon() ||
        norm_b <= std::numeric_limits<double>::epsilon()) {
        return 0.0F;
    }

    const auto sim = dot / (std::sqrt(norm_a) * std::sqrt(norm_b));
    return static_cast<float>(std::clamp(sim, -1.0, 1.0));
}

std::string compose_grounding_context(const std::string& task_type, const std::string& source_text) {
    if (task_type.empty()) {
        return source_text;
    }
    return std::string("task_type: ") + task_type + "\nsource_text: " + source_text;
}

} // namespace

LinepEmbeddingEndpoint::LinepEmbeddingEndpoint(EmbeddingEngine& engine, EmbeddingConfig config)
    : engine_(engine), config_(std::move(config)) {}

LinepEmbeddingEndpoint::~LinepEmbeddingEndpoint() {
    stop();
}

bool LinepEmbeddingEndpoint::start() {
    if (!config_.linep_enabled) {
        status_ = "disabled";
        return false;
    }
    if (running_.exchange(true)) {
        return false;
    }

    linep::pal::net_init();
    heartbeat_sender_ = linep::udp::create_heartbeat_sender(
        static_cast<std::uint16_t>(config_.linep_worker_id),
        static_cast<std::uint8_t>(config_.linep_slot_id));
    if (heartbeat_sender_ != nullptr) {
        update_heartbeat(false);
        heartbeat_sender_->start(
            config_.linep_heartbeat_host.c_str(),
            static_cast<std::uint16_t>(config_.linep_heartbeat_port),
            static_cast<std::uint32_t>(config_.linep_heartbeat_interval_ms));
    }

    tcp_thread_ = std::thread(&LinepEmbeddingEndpoint::run_tcp_loop, this);
    status_ = "running";
    return true;
}

void LinepEmbeddingEndpoint::stop() {
    if (!running_.exchange(false)) {
        return;
    }
    if (heartbeat_sender_ != nullptr) {
        heartbeat_sender_->stop();
        linep::udp::destroy_heartbeat_sender(heartbeat_sender_);
        heartbeat_sender_ = nullptr;
    }
    if (tcp_thread_.joinable()) {
        tcp_thread_.join();
    }
    linep::pal::net_cleanup();
    status_ = "stopped";
}

bool LinepEmbeddingEndpoint::running() const {
    return running_.load();
}

std::string LinepEmbeddingEndpoint::status() const {
    return status_;
}

void LinepEmbeddingEndpoint::update_heartbeat(bool busy) {
    if (heartbeat_sender_ == nullptr) {
        return;
    }

    std::uint8_t flags = linep::SLOT_ALIVE;
    if (engine_.ready()) {
        flags |= linep::SLOT_READY;
    }
    if (busy) {
        flags |= linep::SLOT_BUSY;
    }
    if (engine_.degraded()) {
        flags |= linep::SLOT_DEGRADED;
    }
    if (!engine_.ready()) {
        flags |= linep::SLOT_ERROR;
    }

    const auto load = busy ? 60U : (engine_.ready() ? 5U : 0U);
    heartbeat_sender_->set_status(flags, clamp_byte(load), clamp_byte(queue_depth_.load()));
}

void LinepEmbeddingEndpoint::run_tcp_loop() {
    auto server = linep::pal::tcp_listen(static_cast<std::uint16_t>(config_.linep_tcp_port), 16);
    if (!server.valid()) {
        status_ = "tcp_listen_failed";
        running_.store(false);
        return;
    }

    while (running_.load()) {
        auto client = linep::pal::tcp_accept(server);
        if (!client.valid()) {
            continue;
        }
        std::thread(&LinepEmbeddingEndpoint::handle_client, this, client).detach();
    }
    linep::pal::socket_close(server);
}

void LinepEmbeddingEndpoint::handle_client(linep::pal::Socket client) {
    queue_depth_.fetch_add(1);
    update_heartbeat(true);

    linep::Header header{};
    if (recv_header(client, header) != static_cast<int>(sizeof(header)) ||
        !linep::core::validate_header(header) ||
        !recv_header_extensions(client, header)) {
        linep::pal::socket_close(client);
        queue_depth_.fetch_sub(1);
        update_heartbeat(false);
        return;
    }

    std::string payload;
    payload.resize(header.payload_len);
    if (header.payload_len > 0) {
        if (linep::pal::tcp_recv_all(
                client,
                reinterpret_cast<std::uint8_t*>(&payload[0]),
                static_cast<int>(payload.size())) != static_cast<int>(payload.size())) {
            linep::pal::socket_close(client);
            queue_depth_.fetch_sub(1);
            update_heartbeat(false);
            return;
        }
    }

    const auto worker_id = static_cast<std::uint16_t>(config_.linep_worker_id);
    const auto slot_id = static_cast<std::uint8_t>(config_.linep_slot_id);

    if (header.msg_type != static_cast<std::uint8_t>(linep::MsgType::EMBED_REQUEST) &&
        header.msg_type != static_cast<std::uint8_t>(linep::MsgType::CONSENSUS_REQUEST)) {
        send_frame(
            client,
            static_cast<std::uint8_t>(linep::MsgType::MSG_ERROR),
            linep::FLAG_ERROR,
            header.sequence,
            header.correlation_id,
            worker_id,
            slot_id,
            build_error_payload("unsupported_msg_type", true));
        linep::pal::socket_close(client);
        queue_depth_.fetch_sub(1);
        update_heartbeat(false);
        return;
    }

    try {
        const auto body = json::parse(payload);
        if (header.msg_type == static_cast<std::uint8_t>(linep::MsgType::EMBED_REQUEST)) {
            const std::string input_text = body.value("input_text", "");
            if (input_text.empty()) {
                send_frame(
                    client,
                    static_cast<std::uint8_t>(linep::MsgType::MSG_ERROR),
                    linep::FLAG_ERROR,
                    header.sequence,
                    header.correlation_id,
                    worker_id,
                    slot_id,
                    build_error_payload("empty_input_text", false));
                linep::pal::socket_close(client);
                queue_depth_.fetch_sub(1);
                update_heartbeat(false);
                return;
            }
            const bool normalize = body.value("normalize", engine_.config().normalize_default);
            auto result = engine_.embed(input_text, normalize);
            const auto response = build_embedding_payload(result);
            send_frame(
                client,
                static_cast<std::uint8_t>(linep::MsgType::EMBED_RESPONSE),
                engine_.degraded() ? linep::FLAG_DEGRADED : 0,
                header.sequence,
                header.correlation_id,
                worker_id,
                slot_id,
                response);
        } else {
            const std::string task_type = body.value("task_type", "");
            std::string source_text = body.value("source_text", "");
            if (source_text.empty()) {
                source_text = body.value("context_text", "");
            }
            if (source_text.empty()) {
                send_frame(
                    client,
                    static_cast<std::uint8_t>(linep::MsgType::MSG_ERROR),
                    linep::FLAG_ERROR,
                    header.sequence,
                    header.correlation_id,
                    worker_id,
                    slot_id,
                    build_error_payload("empty_source_text", false));
                linep::pal::socket_close(client);
                queue_depth_.fetch_sub(1);
                update_heartbeat(false);
                return;
            }

            std::vector<std::string> candidates;
            if (body.contains("candidates") && body["candidates"].is_array()) {
                for (const auto& item : body["candidates"]) {
                    if (item.is_string()) {
                        const auto value = item.get<std::string>();
                        if (!value.empty()) {
                            candidates.push_back(value);
                        }
                    }
                }
            } else if (body.contains("answers") && body["answers"].is_array()) {
                for (const auto& item : body["answers"]) {
                    if (item.is_string()) {
                        const auto value = item.get<std::string>();
                        if (!value.empty()) {
                            candidates.push_back(value);
                        }
                    }
                }
            }

            if (candidates.empty()) {
                send_frame(
                    client,
                    static_cast<std::uint8_t>(linep::MsgType::MSG_ERROR),
                    linep::FLAG_ERROR,
                    header.sequence,
                    header.correlation_id,
                    worker_id,
                    slot_id,
                    build_error_payload("empty_candidates", false));
                linep::pal::socket_close(client);
                queue_depth_.fetch_sub(1);
                update_heartbeat(false);
                return;
            }

            const float threshold = clamp01(body.value("threshold", 0.65F));
            float context_weight = clamp01(body.value("context_weight", 0.80F));
            float consensus_weight = clamp01(body.value("consensus_weight", 0.20F));
            const float context_min_similarity = clamp01(body.value("context_min_similarity", 0.40F));
            const auto weight_sum = context_weight + consensus_weight;
            if (weight_sum <= std::numeric_limits<float>::epsilon()) {
                context_weight = 0.80F;
                consensus_weight = 0.20F;
            } else {
                context_weight /= weight_sum;
                consensus_weight /= weight_sum;
            }

            const auto response = build_consensus_payload(
                task_type,
                source_text,
                candidates,
                threshold,
                context_weight,
                consensus_weight,
                context_min_similarity);
            send_frame(
                client,
                static_cast<std::uint8_t>(linep::MsgType::CONSENSUS_RESPONSE),
                engine_.degraded() ? linep::FLAG_DEGRADED : 0,
                header.sequence,
                header.correlation_id,
                worker_id,
                slot_id,
                response);
        }
    } catch (const std::exception& ex) {
        send_frame(
            client,
            static_cast<std::uint8_t>(linep::MsgType::MSG_ERROR),
            linep::FLAG_ERROR,
            header.sequence,
            header.correlation_id,
            worker_id,
            slot_id,
            build_error_payload(ex.what(), true));
    }

    linep::pal::socket_close(client);
    queue_depth_.fetch_sub(1);
    update_heartbeat(false);
}

std::string LinepEmbeddingEndpoint::build_error_payload(const std::string& error, bool degraded) const {
    return json({
        {"status", "failed"},
        {"error", error},
        {"degraded", degraded},
        {"runtime", "openvino-cpp"},
        {"slot_type", "embedding"}
    }).dump();
}

std::string LinepEmbeddingEndpoint::build_embedding_payload(const EmbeddingResult& result) const {
    return json({
        {"item", {
            {"model", result.model},
            {"dimensions", result.dimensions},
            {"vector", result.vector},
            {"metadata", {
                {"runtime", result.runtime_backend},
                {"transport", "linep"},
                {"device", result.device},
                {"normalized", result.normalized},
                {"embedding_latency_ms", std::round(result.infer_ms * 1000.0) / 1000.0},
                {"slot_type", "embedding"}
            }}
        }},
        {"status", {
            {"status", engine_.degraded() ? "partial" : "success"},
            {"backend", "embedding"},
            {"degraded", engine_.degraded()},
            {"error", engine_.degraded() ? engine_.error() : ""}
        }}
    }).dump();
}

std::string LinepEmbeddingEndpoint::build_consensus_payload(
    const std::string& task_type,
    const std::string& source_text,
    const std::vector<std::string>& candidates,
    float threshold,
    float context_weight,
    float consensus_weight,
    float context_min_similarity) const {
    const auto context_text = compose_grounding_context(task_type, source_text);
    const auto context_embedding = engine_.embed(context_text, true).vector;

    std::vector<std::vector<float>> candidate_embeddings;
    candidate_embeddings.reserve(candidates.size());
    for (const auto& candidate : candidates) {
        candidate_embeddings.push_back(engine_.embed(candidate, true).vector);
    }

    std::vector<float> pair_avg(candidates.size(), 0.0F);
    if (candidates.size() > 1) {
        for (std::size_t i = 0; i < candidates.size(); ++i) {
            float sum = 0.0F;
            std::size_t count = 0;
            for (std::size_t j = 0; j < candidates.size(); ++j) {
                if (i == j) {
                    continue;
                }
                sum += cosine_similarity(candidate_embeddings[i], candidate_embeddings[j]);
                count++;
            }
            pair_avg[i] = count > 0 ? sum / static_cast<float>(count) : 0.0F;
        }
    }

    json score_items = json::array();
    std::size_t best_index = 0;
    float best_score = -1.0F;
    std::size_t accepted_count = 0;

    for (std::size_t i = 0; i < candidates.size(); ++i) {
        const float context_similarity = cosine_similarity(context_embedding, candidate_embeddings[i]);
        const float denominator = std::max(1.0F - context_min_similarity, 0.05F);
        const float context_gate = clamp01((context_similarity - context_min_similarity) / denominator);
        const float grounded_consensus = pair_avg[i] * context_gate;
        const float disagreement_penalty =
            std::max(0.0F, pair_avg[i] - context_similarity) * 0.35F;

        float final_score =
            (context_weight * context_similarity) +
            (consensus_weight * grounded_consensus) -
            disagreement_penalty;
        final_score = clamp01(final_score);

        const bool accepted = final_score >= threshold && context_similarity >= context_min_similarity;
        if (accepted) {
            accepted_count++;
        }

        if (final_score > best_score) {
            best_score = final_score;
            best_index = i;
        }

        score_items.push_back({
            {"index", i},
            {"context_similarity", context_similarity},
            {"pair_avg_similarity", pair_avg[i]},
            {"grounded_consensus", grounded_consensus},
            {"disagreement_penalty", disagreement_penalty},
            {"final_score", final_score},
            {"accepted", accepted},
            {"candidate_preview", candidates[i].substr(0, 240)}
        });
    }

    std::uint8_t consensus_level = static_cast<std::uint8_t>(linep::ConsensusLevel::CONSENSUS_FAILED);
    if (accepted_count >= 3) {
        consensus_level = static_cast<std::uint8_t>(linep::ConsensusLevel::CONSENSUS_STRONG);
    } else if (accepted_count >= 2) {
        consensus_level = static_cast<std::uint8_t>(linep::ConsensusLevel::CONSENSUS_PARTIAL);
    }

    const bool accepted = accepted_count >= 2 && best_score >= threshold;
    return json({
        {"task_type", task_type},
        {"source_text", source_text},
        {"context_text", context_text},
        {"candidate_count", candidates.size()},
        {"threshold", threshold},
        {"context_min_similarity", context_min_similarity},
        {"weights", {
            {"context", context_weight},
            {"consensus", consensus_weight}
        }},
        {"consensus_level", consensus_level},
        {"best_index", best_index},
        {"best_score", std::max(0.0F, best_score)},
        {"accepted", accepted},
        {"accepted_count", accepted_count},
        {"scores", score_items},
        {"status", {
            {"status", accepted ? "success" : "partial"},
            {"backend", "embedding_consensus"},
            {"degraded", engine_.degraded()},
            {"error", engine_.degraded() ? engine_.error() : ""}
        }}
    }).dump();
}

} // namespace liara::embedding
