#include "../src/core/framing.hpp"
#include <linep/types.hpp>
#include <linep/messages.hpp>
#include <cassert>
#include <cstdio>
#include <cstring>

static void test_sizes() {
    static_assert(sizeof(linep::HeartbeatCompact) == 12,
                  "HeartbeatCompact must be 12 bytes");
    static_assert(sizeof(linep::Header) == 24,
                  "Header must be 24 bytes");
}

static void test_compact_fields() {
    auto f = linep::core::make_heartbeat_compact(
        /*worker_id*/   17u,
        /*slot_id*/      1u,
        /*slot_flags*/   static_cast<uint8_t>(linep::SLOT_ALIVE | linep::SLOT_READY),
        /*load*/        42u,
        /*queue_depth*/  3u,
        /*sequence*/     0u);

    assert(f.magic       == linep::MAGIC);
    assert(f.version     == linep::VERSION);
    assert(f.msg_type    == static_cast<uint8_t>(linep::MsgType::HEARTBEAT));
    assert(f.worker_id   == 17u);
    assert(f.slot_id     ==  1u);
    assert(f.load        == 42u);
    assert(f.queue_depth ==  3u);
    assert(f.sequence    ==  0u);
}

static void test_compact_validate_ok() {
    auto f = linep::core::make_heartbeat_compact(7u, 0u, linep::SLOT_ALIVE, 10u, 0u, 5u);
    assert(linep::core::validate_heartbeat_compact(f));
}

static void test_compact_reject_corrupted_field() {
    auto f = linep::core::make_heartbeat_compact(7u, 0u, linep::SLOT_ALIVE, 10u, 0u, 5u);

    // Modify a field without recomputing CRC — must be rejected.
    linep::HeartbeatCompact bad = f;
    bad.load = 99u;
    assert(!linep::core::validate_heartbeat_compact(bad));
}

static void test_compact_reject_bad_magic() {
    auto f = linep::core::make_heartbeat_compact(7u, 0u, linep::SLOT_ALIVE, 10u, 0u, 5u);
    linep::HeartbeatCompact bad = f;
    bad.magic = 0xDEADu;
    assert(!linep::core::validate_heartbeat_compact(bad));
}

static void test_compact_reject_wrong_version() {
    auto f = linep::core::make_heartbeat_compact(7u, 0u, linep::SLOT_ALIVE, 10u, 0u, 5u);
    linep::HeartbeatCompact bad = f;
    bad.version = 0x02u;
    assert(!linep::core::validate_heartbeat_compact(bad));
}

static void test_header_validate_ok() {
    auto h = linep::core::make_header(
        static_cast<uint8_t>(linep::MsgType::TASK),
        linep::FLAG_ACK_REQUIRED,
        /*payload_len*/   256u,
        /*sequence*/        1u,
        /*correlation_id*/  0u,
        /*worker_id*/      42u,
        /*slot_id*/         0u);

    assert(h.magic      == linep::MAGIC);
    assert(h.version    == linep::VERSION);
    assert(h.header_len == 24u);
    assert(linep::core::validate_header(h));
}

static void test_header_reject_corrupted() {
    auto h = linep::core::make_header(
        static_cast<uint8_t>(linep::MsgType::PING),
        0u, 0u, 0u, 0u, 1u, 0u);
    linep::Header bad = h;
    bad.worker_id = 999u;  // changed without recomputing CRC
    assert(!linep::core::validate_header(bad));
}

int main() {
    test_sizes();
    test_compact_fields();
    test_compact_validate_ok();
    test_compact_reject_corrupted_field();
    test_compact_reject_bad_magic();
    test_compact_reject_wrong_version();
    test_header_validate_ok();
    test_header_reject_corrupted();
    std::puts("[PASS] test_heartbeat_compact");
    return 0;
}
