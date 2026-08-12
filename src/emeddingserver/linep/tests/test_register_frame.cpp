#include "../src/core/framing.hpp"
#include <linep/messages.hpp>
#include <linep/types.hpp>

#include <cassert>
#include <cstdint>
#include <cstdio>

int main() {
    const uint32_t payload_len = 6u;
    const auto h = linep::core::make_header(
        static_cast<uint8_t>(linep::MsgType::REGISTER),
        linep::FLAG_ACK_REQUIRED,
        payload_len,
        11u,
        77u,
        123u,
        2u);

    assert(h.magic == linep::MAGIC);
    assert(h.version == linep::VERSION);
    assert(h.msg_type == static_cast<uint8_t>(linep::MsgType::REGISTER));
    assert(h.flags == linep::FLAG_ACK_REQUIRED);
    assert(h.payload_len == payload_len);
    assert(h.sequence == 11u);
    assert(h.correlation_id == 77u);
    assert(h.worker_id == 123u);
    assert(h.slot_id == 2u);
    assert(linep::core::validate_header(h));

    std::puts("[PASS] test_register_frame");
    return 0;
}
