#pragma once
#include <cstdint>
#include <cstddef>

namespace linep::core {

// CRC-8 (poly=0x07, init=0x00, no reflection)
// Used for:
//   HeartbeatCompact.crc8  — over bytes [0..10]
//   Header.header_crc      — over bytes [0..22]
uint8_t crc8(const uint8_t* data, size_t len) noexcept;

} // namespace linep::core
