#pragma once

#include "embedding_engine.hpp"
#include "linep/src/pal/socket.hpp"

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <thread>

namespace linep::udp {
class IHeartbeatSender;
}

namespace liara::embedding {

class LinepEmbeddingEndpoint {
public:
    LinepEmbeddingEndpoint(EmbeddingEngine& engine, EmbeddingConfig config);
    ~LinepEmbeddingEndpoint();

    bool start();
    void stop();
    bool running() const;
    std::string status() const;

private:
    void run_tcp_loop();
    void handle_client(linep::pal::Socket client);
    void update_heartbeat(bool busy);
    std::string build_error_payload(const std::string& error, bool degraded) const;
    std::string build_embedding_payload(const EmbeddingResult& result) const;
    std::string build_consensus_payload(
        const std::string& task_type,
        const std::string& source_text,
        const std::vector<std::string>& candidates,
        float threshold,
        float context_weight,
        float consensus_weight,
        float context_min_similarity) const;

    EmbeddingEngine& engine_;
    EmbeddingConfig config_;
    std::atomic<bool> running_{false};
    std::atomic<unsigned int> queue_depth_{0};
    std::thread tcp_thread_;
    linep::udp::IHeartbeatSender* heartbeat_sender_ = nullptr;
    std::string status_ = "stopped";
};

} // namespace liara::embedding
