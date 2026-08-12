#include "../src/core/framing.hpp"
#include <linep/messages.hpp>
#include <linep/types.hpp>

#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

int main() {
    const uint16_t err = static_cast<uint16_t>(linep::ERR_TIMEOUT);
    const std::string reason = "backend timeout";

    std::vector<uint8_t> payload(2u);
    std::memcpy(payload.data(), &err, sizeof(err));
    payload.insert(payload.end(), reason.begin(), reason.end());

    const auto h = linep::core::make_header(
        static_cast<uint8_t>(linep::MsgType::MSG_ERROR),
        linep::FLAG_ERROR,
        static_cast<uint32_t>(payload.size()),
        17u,
        17u,
        8u,
        0u);

    assert(linep::core::validate_header(h));
    assert(h.msg_type == static_cast<uint8_t>(linep::MsgType::MSG_ERROR));
    assert((h.flags & linep::FLAG_ERROR) != 0u);

    uint16_t parsed_code = 0u;
    std::memcpy(&parsed_code, payload.data(), sizeof(parsed_code));
    const std::string parsed_reason(payload.begin() + 2, payload.end());

    assert(parsed_code == static_cast<uint16_t>(linep::ERR_TIMEOUT));
    assert(parsed_reason == reason);

    std::puts("[PASS] test_error_frame_handling");
    return 0;
}
