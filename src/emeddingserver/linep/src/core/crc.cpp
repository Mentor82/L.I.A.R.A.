#include "crc.hpp"

namespace linep::core {

uint8_t crc8(const uint8_t* data, size_t len) noexcept {
    uint8_t crc = 0x00u;
    while (len--) {
        crc ^= *data++;
        for (int i = 0; i < 8; ++i) {
            crc = (crc & 0x80u) ? static_cast<uint8_t>((crc << 1u) ^ 0x07u)
                                : static_cast<uint8_t>( crc << 1u);
        }
    }
    return crc;
}

} // namespace linep::core
