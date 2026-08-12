#include "../src/pal/socket.hpp"

#include <cassert>
#include <chrono>
#include <cstdio>

int main() {
    linep::pal::net_init();

    linep::pal::Socket s = linep::pal::udp_open();
    assert(s.valid());
    assert(linep::pal::udp_bind(s, 39061u));
    linep::pal::udp_set_recv_timeout(s, 120u);

    uint8_t buf[32]{};
    char src_ip[64]{};
    uint16_t src_port = 0u;

    const auto t0 = std::chrono::steady_clock::now();
    const int r = linep::pal::udp_recvfrom(s, buf, static_cast<int>(sizeof(buf)), src_ip, static_cast<int>(sizeof(src_ip)), &src_port);
    const auto t1 = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();

    // Timeout should not return a successful receive.
    assert(r <= 0);
    // OS scheduling jitter is expected; require a lower bound only.
    assert(elapsed_ms >= 80);
    assert(elapsed_ms < 2000);

    linep::pal::socket_close(s);
    linep::pal::net_cleanup();

    std::puts("[PASS] test_timeout_detection");
    return 0;
}
