#pragma once
#include <linep/types.hpp>
#include <cstdint>

namespace linep::core {

// ── HeartbeatCompact ─────────────────────────────────────────────────────────

// Build a HeartbeatCompact frame and compute its CRC-8.
linep::HeartbeatCompact make_heartbeat_compact(
    uint16_t worker_id,
    uint8_t  slot_id,
    uint8_t  slot_flags,
    uint8_t  load,
    uint8_t  queue_depth,
    uint8_t  sequence) noexcept;

// Validate magic, version, msg_type and CRC-8.
bool validate_heartbeat_compact(
    const linep::HeartbeatCompact& f) noexcept;

// ── Common Header ─────────────────────────────────────────────────────────────

// Build a Header and compute its CRC-8.
linep::Header make_header(
    uint8_t  msg_type,
    uint16_t flags,
    uint32_t payload_len,
    uint32_t sequence,
    uint32_t correlation_id,
    uint16_t worker_id,
    uint8_t  slot_id) noexcept;

// Build-time extension helpers (v1.1).
linep::HeaderBuildTimeExt make_build_time_ext_from_build() noexcept;

// Mutates header to include build-time extension metadata and recomputes CRC.
void apply_build_time_extension(linep::Header& h) noexcept;

// Parse extension bytes (if present) into HeaderBuildTimeExt.
bool try_parse_build_time_ext(const uint8_t* ext,
                              uint16_t ext_len,
                              linep::HeaderBuildTimeExt& out) noexcept;

// Validate magic, version, header_len and CRC-8.
bool validate_header(const linep::Header& h) noexcept;

} // namespace linep::core
