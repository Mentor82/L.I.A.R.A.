#pragma once
#include <cstdint>
#include <cstring>

// LiNeP wire format is always Little-Endian.
// On x64 and ARM64 (both LE) every function is a no-op.
// A future Big-Endian port only needs to change this file.

namespace linep::pal {

inline uint16_t to_le16(uint16_t v) noexcept {
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    return static_cast<uint16_t>((v << 8u) | (v >> 8u));
#else
    return v;
#endif
}

inline uint32_t to_le32(uint32_t v) noexcept {
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    return ((v & 0xFF000000u) >> 24u) | ((v & 0x00FF0000u) >> 8u)
         | ((v & 0x0000FF00u) <<  8u) | ((v & 0x000000FFu) << 24u);
#else
    return v;
#endif
}

inline float to_le_float(float f) noexcept {
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    uint32_t tmp;
    std::memcpy(&tmp, &f, 4);
    tmp = to_le32(tmp);
    std::memcpy(&f, &tmp, 4);
#endif
    return f;
}

} // namespace linep::pal
