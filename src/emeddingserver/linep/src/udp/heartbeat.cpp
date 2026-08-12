#include "heartbeat.hpp"
#include "../pal/socket.hpp"
#include "../pal/clock.hpp"
#include "../core/framing.hpp"

#include <atomic>
#include <string>
#include <thread>

namespace linep::udp {

// ── Heartbeat Sender implementation (hidden, not exported) ───────────────────

class HeartbeatSenderImpl final : public IHeartbeatSender {
public:
    HeartbeatSenderImpl(uint16_t worker_id, uint8_t slot_id)
        : worker_id_(worker_id), slot_id_(slot_id) {}

    ~HeartbeatSenderImpl() override { stop(); }

    bool start(const char* target_ip, uint16_t target_port,
               uint32_t interval_ms) override {
        if (running_.load()) return false;
        target_ip_   = target_ip;
        target_port_ = target_port;
        interval_ms_ = interval_ms;
        running_.store(true);
        thread_ = std::thread(&HeartbeatSenderImpl::run, this);
        return true;
    }

    void stop() override {
        running_.store(false);
        if (thread_.joinable()) thread_.join();
    }

    void set_status(uint8_t slot_flags, uint8_t load,
                    uint8_t queue_depth) override {
        slot_flags_.store(slot_flags);
        load_.store(load);
        queue_depth_.store(queue_depth);
    }

private:
    void run() {
        pal::Socket sock = pal::udp_open();
        if (!sock.valid()) { running_.store(false); return; }

        while (running_.load()) {
            auto frame = core::make_heartbeat_compact(
                worker_id_, slot_id_,
                slot_flags_.load(),
                load_.load(),
                queue_depth_.load(),
                seq_++);

            pal::udp_sendto(sock,
                target_ip_.c_str(), target_port_,
                reinterpret_cast<const uint8_t*>(&frame),
                static_cast<int>(sizeof(frame)));

            // Sleep in 50 ms ticks so stop() reacts quickly.
            uint32_t slept = 0;
            while (running_.load() && slept < interval_ms_) {
                pal::sleep_ms(50u);
                slept += 50u;
            }
        }
        pal::socket_close(sock);
    }

    uint16_t             worker_id_;
    uint8_t              slot_id_;
    std::string          target_ip_;
    uint16_t             target_port_{0};
    uint32_t             interval_ms_{1000};
    std::atomic<uint8_t> slot_flags_{0};
    std::atomic<uint8_t> load_{0};
    std::atomic<uint8_t> queue_depth_{0};
    std::atomic<uint8_t> seq_{0};
    std::atomic<bool>    running_{false};
    std::thread          thread_;
};

// ── Heartbeat Receiver implementation (hidden, not exported) ─────────────────

class HeartbeatReceiverImpl final : public IHeartbeatReceiver {
public:
    HeartbeatReceiverImpl() = default;
    ~HeartbeatReceiverImpl() override { stop(); }

    bool start(uint16_t port, Callback cb, void* user_data) override {
        if (running_.load()) return false;
        port_      = port;
        callback_  = cb;
        user_data_ = user_data;
        running_.store(true);
        thread_ = std::thread(&HeartbeatReceiverImpl::run, this);
        return true;
    }

    void stop() override {
        running_.store(false);
        if (thread_.joinable()) thread_.join();
    }

private:
    void run() {
        pal::Socket sock = pal::udp_open();
        if (!sock.valid()) { running_.store(false); return; }

        pal::udp_bind(sock, port_);
        // 500 ms receive timeout so the loop can check running_ regularly.
        pal::udp_set_recv_timeout(sock, 500u);

        linep::HeartbeatCompact frame{};
        char     src_ip[64]{};
        uint16_t src_port{};

        while (running_.load()) {
            int r = pal::udp_recvfrom(
                sock,
                reinterpret_cast<uint8_t*>(&frame),
                static_cast<int>(sizeof(frame)),
                src_ip, static_cast<int>(sizeof(src_ip)),
                &src_port);

            if (r == static_cast<int>(sizeof(linep::HeartbeatCompact))) {
                if (core::validate_heartbeat_compact(frame) && callback_) {
                    callback_(frame, src_ip, src_port, user_data_);
                }
            }
        }
        pal::socket_close(sock);
    }

    uint16_t          port_{0};
    Callback          callback_{nullptr};
    void*             user_data_{nullptr};
    std::atomic<bool> running_{false};
    std::thread       thread_;
};

// ── Factory functions ─────────────────────────────────────────────────────────

IHeartbeatSender* create_heartbeat_sender(uint16_t worker_id, uint8_t slot_id) {
    return new HeartbeatSenderImpl(worker_id, slot_id);
}
void destroy_heartbeat_sender(IHeartbeatSender* p) { delete p; }

IHeartbeatReceiver* create_heartbeat_receiver() {
    return new HeartbeatReceiverImpl();
}
void destroy_heartbeat_receiver(IHeartbeatReceiver* p) { delete p; }

} // namespace linep::udp
