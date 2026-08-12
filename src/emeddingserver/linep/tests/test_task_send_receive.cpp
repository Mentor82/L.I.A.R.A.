#include "../src/core/framing.hpp"
#include "../src/pal/socket.hpp"
#include <linep/messages.hpp>
#include <linep/types.hpp>

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

namespace {

uint16_t pick_listen_port() {
    for (uint16_t port = 39100u; port < 39140u; ++port) {
        linep::pal::Socket probe = linep::pal::tcp_listen(port, 1);
        if (probe.valid()) {
            linep::pal::socket_close(probe);
            return port;
        }
    }
    return 0u;
}

} // namespace

int main() {
    linep::pal::net_init();

    const uint16_t port = pick_listen_port();
    assert(port != 0u);

    bool server_ok = false;
    std::thread server([&]() {
        linep::pal::Socket ls = linep::pal::tcp_listen(port, 1);
        if (!ls.valid()) return;

        linep::pal::Socket cs = linep::pal::tcp_accept(ls);
        if (!cs.valid()) {
            linep::pal::socket_close(ls);
            return;
        }

        linep::Header in_h{};
        int r = linep::pal::tcp_recv_all(cs, reinterpret_cast<uint8_t*>(&in_h), static_cast<int>(sizeof(in_h)));
        if (r != static_cast<int>(sizeof(in_h)) || !linep::core::validate_header(in_h)) {
            linep::pal::socket_close(cs);
            linep::pal::socket_close(ls);
            return;
        }

        std::vector<uint8_t> in_payload(in_h.payload_len);
        if (!in_payload.empty()) {
            r = linep::pal::tcp_recv_all(cs, in_payload.data(), static_cast<int>(in_payload.size()));
            if (r != static_cast<int>(in_payload.size())) {
                linep::pal::socket_close(cs);
                linep::pal::socket_close(ls);
                return;
            }
        }

        if (in_h.msg_type != static_cast<uint8_t>(linep::MsgType::TASK)) {
            linep::pal::socket_close(cs);
            linep::pal::socket_close(ls);
            return;
        }

        std::vector<uint8_t> out_payload;
        out_payload.push_back(static_cast<uint8_t>(linep::RESULT_OK));
        const char* txt = "done";
        out_payload.insert(out_payload.end(), txt, txt + std::strlen(txt));

        const auto out_h = linep::core::make_header(
            static_cast<uint8_t>(linep::MsgType::RESULT),
            0u,
            static_cast<uint32_t>(out_payload.size()),
            in_h.sequence + 1u,
            in_h.correlation_id,
            in_h.worker_id,
            in_h.slot_id);

        r = linep::pal::tcp_send_all(cs, reinterpret_cast<const uint8_t*>(&out_h), static_cast<int>(sizeof(out_h)));
        if (r != static_cast<int>(sizeof(out_h))) {
            linep::pal::socket_close(cs);
            linep::pal::socket_close(ls);
            return;
        }
        r = linep::pal::tcp_send_all(cs, out_payload.data(), static_cast<int>(out_payload.size()));
        if (r != static_cast<int>(out_payload.size())) {
            linep::pal::socket_close(cs);
            linep::pal::socket_close(ls);
            return;
        }

        server_ok = true;
        linep::pal::socket_close(cs);
        linep::pal::socket_close(ls);
    });

    std::this_thread::sleep_for(std::chrono::milliseconds(30));

    linep::pal::Socket c = linep::pal::tcp_connect("127.0.0.1", port, 1000);
    assert(c.valid());

    const std::string task_text = "sum";
    std::vector<uint8_t> task_payload(task_text.begin(), task_text.end());

    const auto task_h = linep::core::make_header(
        static_cast<uint8_t>(linep::MsgType::TASK),
        linep::FLAG_ACK_REQUIRED,
        static_cast<uint32_t>(task_payload.size()),
        100u,
        555u,
        9u,
        1u);

    int r = linep::pal::tcp_send_all(c, reinterpret_cast<const uint8_t*>(&task_h), static_cast<int>(sizeof(task_h)));
    assert(r == static_cast<int>(sizeof(task_h)));
    r = linep::pal::tcp_send_all(c, task_payload.data(), static_cast<int>(task_payload.size()));
    assert(r == static_cast<int>(task_payload.size()));

    linep::Header res_h{};
    r = linep::pal::tcp_recv_all(c, reinterpret_cast<uint8_t*>(&res_h), static_cast<int>(sizeof(res_h)));
    assert(r == static_cast<int>(sizeof(res_h)));
    assert(linep::core::validate_header(res_h));
    assert(res_h.msg_type == static_cast<uint8_t>(linep::MsgType::RESULT));

    std::vector<uint8_t> res_payload(res_h.payload_len);
    r = linep::pal::tcp_recv_all(c, res_payload.data(), static_cast<int>(res_payload.size()));
    assert(r == static_cast<int>(res_payload.size()));
    assert(!res_payload.empty());

    const auto st = static_cast<linep::ResultStatus>(res_payload[0]);
    const std::string txt(res_payload.begin() + 1, res_payload.end());
    assert(st == linep::RESULT_OK);
    assert(txt == "done");

    linep::pal::socket_close(c);
    server.join();
    assert(server_ok);

    linep::pal::net_cleanup();

    std::puts("[PASS] test_task_send_receive");
    return 0;
}
