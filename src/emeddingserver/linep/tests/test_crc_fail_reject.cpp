#include "../src/core/framing.hpp"
#include <linep/messages.hpp>
#include <linep/types.hpp>

#include <cassert>
#include <cstdint>
#include <cstdio>

int main() {
    auto h = linep::core::make_header(
        static_cast<uint8_t>(linep::MsgType::REGISTER),
        0u,
        0u,
        1u,
        1u,
        1u,
        0u);

    assert(linep::core::validate_header(h));

    // Corrupt exactly one protected byte and keep stale CRC.
    h.sequence ^= 0x01u;
    assert(!linep::core::validate_header(h));

    auto hb = linep::core::make_heartbeat_compact(1u, 0u, linep::SLOT_ALIVE, 10u, 0u, 1u);
    assert(linep::core::validate_heartbeat_compact(hb));
    hb.queue_depth ^= 0x01u;
    assert(!linep::core::validate_heartbeat_compact(hb));

    std::puts("[PASS] test_crc_fail_reject");
    return 0;
}
