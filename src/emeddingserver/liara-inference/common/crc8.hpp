#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

namespace liara::common {

constexpr std::uint8_t kCrc8Polynomial = 0x07;

inline std::uint8_t crc8_update(std::uint8_t crc, const std::uint8_t value) {
    crc ^= value;
    for (int i = 0; i < 8; ++i) {
        if ((crc & 0x80U) != 0U) {
            crc = static_cast<std::uint8_t>((crc << 1U) ^ kCrc8Polynomial);
        } else {
            crc = static_cast<std::uint8_t>(crc << 1U);
        }
    }
    return crc;
}

template <std::size_t N>
inline std::uint8_t crc8(const std::array<std::uint8_t, N>& bytes, const std::size_t count) {
    std::uint8_t crc = 0;
    for (std::size_t i = 0; i < count; ++i) {
        crc = crc8_update(crc, bytes[i]);
    }
    return crc;
}

} // namespace liara::common
