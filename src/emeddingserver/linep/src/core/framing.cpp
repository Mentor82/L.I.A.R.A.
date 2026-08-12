#include "framing.hpp"
#include "crc.hpp"
#include <linep/messages.hpp>
#include <cstring>

#ifndef LINEP_BUILD_YEAR_2D
#define LINEP_BUILD_YEAR_2D 0
#endif
#ifndef LINEP_BUILD_MONTH
#define LINEP_BUILD_MONTH 1
#endif
#ifndef LINEP_BUILD_DAY
#define LINEP_BUILD_DAY 1
#endif
#ifndef LINEP_BUILD_HOUR
#define LINEP_BUILD_HOUR 0
#endif
#ifndef LINEP_BUILD_MINUTE
#define LINEP_BUILD_MINUTE 0
#endif
#ifndef LINEP_BUILD_SECOND
#define LINEP_BUILD_SECOND 0
#endif

namespace linep::core {

// ── HeartbeatCompact ─────────────────────────────────────────────────────────

linep::HeartbeatCompact make_heartbeat_compact(
    uint16_t worker_id, uint8_t slot_id,
    uint8_t  slot_flags, uint8_t load,
    uint8_t  queue_depth, uint8_t sequence) noexcept
{
    linep::HeartbeatCompact f{};
    f.magic       = linep::MAGIC;
    f.version     = linep::VERSION;
    f.msg_type    = static_cast<uint8_t>(linep::MsgType::HEARTBEAT);
    f.worker_id   = worker_id;
    f.slot_id     = slot_id;
    f.slot_flags  = slot_flags;
    f.load        = load;
    f.queue_depth = queue_depth;
    f.sequence    = sequence;
    // CRC-8 covers bytes [0..10] (all fields except crc8 itself)
    f.crc8 = crc8(reinterpret_cast<const uint8_t*>(&f), 11u);
    return f;
}

bool validate_heartbeat_compact(
    const linep::HeartbeatCompact& f) noexcept
{
    if (f.magic    != linep::MAGIC)   return false;
    if (f.version  != linep::VERSION) return false;
    if (f.msg_type != static_cast<uint8_t>(linep::MsgType::HEARTBEAT)) return false;
    const uint8_t expected = crc8(reinterpret_cast<const uint8_t*>(&f), 11u);
    return f.crc8 == expected;
}

// ── Common Header ─────────────────────────────────────────────────────────────

linep::Header make_header(
    uint8_t  msg_type, uint16_t flags,
    uint32_t payload_len, uint32_t sequence,
    uint32_t correlation_id, uint16_t worker_id, uint8_t slot_id) noexcept
{
    linep::Header h{};
    h.magic          = linep::MAGIC;
    h.version        = linep::VERSION;
    h.msg_type       = msg_type;
    h.header_len     = static_cast<uint16_t>(sizeof(linep::Header));
    h.flags          = flags;
    h.payload_len    = payload_len;
    h.sequence       = sequence;
    h.correlation_id = correlation_id;
    h.worker_id      = worker_id;
    h.slot_id        = slot_id;
    // CRC-8 covers bytes [0..22] (header_crc is byte 23)
    h.header_crc = crc8(reinterpret_cast<const uint8_t*>(&h), 23u);
    return h;
}

linep::HeaderBuildTimeExt make_build_time_ext_from_build() noexcept {
    linep::HeaderBuildTimeExt ext{};
    ext.year_2d = static_cast<uint8_t>(LINEP_BUILD_YEAR_2D);
    ext.month   = static_cast<uint8_t>(LINEP_BUILD_MONTH);
    ext.day     = static_cast<uint8_t>(LINEP_BUILD_DAY);
    ext.hour    = static_cast<uint8_t>(LINEP_BUILD_HOUR);
    ext.minute  = static_cast<uint8_t>(LINEP_BUILD_MINUTE);
    ext.second  = static_cast<uint8_t>(LINEP_BUILD_SECOND);
    return ext;
}

void apply_build_time_extension(linep::Header& h) noexcept {
    h.flags = static_cast<uint16_t>(h.flags | static_cast<uint16_t>(linep::FLAG_BUILD_TIME));
    h.header_len = static_cast<uint16_t>(linep::HEADER_BASE_LEN + linep::HEADER_BUILD_TIME_LEN);
    h.header_crc = crc8(reinterpret_cast<const uint8_t*>(&h), 23u);
}

bool try_parse_build_time_ext(const uint8_t* ext,
                              uint16_t ext_len,
                              linep::HeaderBuildTimeExt& out) noexcept {
    if (!ext || ext_len < linep::HEADER_BUILD_TIME_LEN) return false;
    std::memcpy(&out, ext, sizeof(linep::HeaderBuildTimeExt));
    return true;
}

bool validate_header(const linep::Header& h) noexcept {
    if (h.magic      != linep::MAGIC)   return false;
    if (h.version    != linep::VERSION) return false;
    if (h.header_len < linep::HEADER_BASE_LEN) return false;
    if (h.header_len > static_cast<uint16_t>(linep::HEADER_BASE_LEN + linep::HEADER_BUILD_TIME_LEN)) return false;
    const uint8_t expected = crc8(reinterpret_cast<const uint8_t*>(&h), 23u);
    return h.header_crc == expected;
}

} // namespace linep::core
