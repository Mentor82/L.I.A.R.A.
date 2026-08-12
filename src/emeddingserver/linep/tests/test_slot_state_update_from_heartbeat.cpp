#include "../src/core/framing.hpp"
#include "../src/pal/socket.hpp"
#include "../src/udp/heartbeat.hpp"
#include <linep/messages.hpp>
#include <linep/types.hpp>

#include <cassert>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <mutex>

namespace {

struct CallbackState {
    std::mutex m;
    std::condition_variable cv;
    bool got_expected{false};
    linep::HeartbeatCompact last{};
};

void on_heartbeat(const linep::HeartbeatCompact& f, const char*, uint16_t, void* user_data) {
    auto* st = static_cast<CallbackState*>(user_data);
    {
        std::lock_guard<std::mutex> lk(st->m);
        st->last = f;
        const uint8_t expected_flags = static_cast<uint8_t>(linep::SLOT_ALIVE | linep::SLOT_READY | linep::SLOT_BUSY);
        if (f.slot_flags == expected_flags && f.load == 73u && f.queue_depth == 9u) {
            st->got_expected = true;
        }
    }
    st->cv.notify_one();
}

} // namespace

int main() {
    linep::pal::net_init();

    auto* rx = linep::udp::create_heartbeat_receiver();
    auto* tx = linep::udp::create_heartbeat_sender(44u, 3u);
    assert(rx != nullptr);
    assert(tx != nullptr);

    CallbackState st{};
    const uint16_t port = 39071u;

    const bool rx_started = rx->start(port, &on_heartbeat, &st);
    assert(rx_started);

    const bool tx_started = tx->start("127.0.0.1", port, 80u);
    assert(tx_started);

    tx->set_status(static_cast<uint8_t>(linep::SLOT_ALIVE | linep::SLOT_READY | linep::SLOT_BUSY), 73u, 9u);

    {
        std::unique_lock<std::mutex> lk(st.m);
        const bool ok = st.cv.wait_for(lk, std::chrono::seconds(2), [&]() { return st.got_expected; });
        assert(ok);
        assert(linep::core::validate_heartbeat_compact(st.last));
    }

    tx->stop();
    rx->stop();
    linep::udp::destroy_heartbeat_sender(tx);
    linep::udp::destroy_heartbeat_receiver(rx);
    linep::pal::net_cleanup();

    std::puts("[PASS] test_slot_state_update_from_heartbeat");
    return 0;
}
