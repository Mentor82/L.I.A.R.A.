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
    const std::string text = "ok";
    std::vector<uint8_t> payload;
    payload.push_back(static_cast<uint8_t>(linep::RESULT_OK));
    payload.insert(payload.end(), text.begin(), text.end());

    const auto h = linep::core::make_header(
        static_cast<uint8_t>(linep::MsgType::RESULT),
        0u,
        static_cast<uint32_t>(payload.size()),
        42u,
        900u,
        5u,
        1u);

    assert(linep::core::validate_header(h));
    assert(h.msg_type == static_cast<uint8_t>(linep::MsgType::RESULT));
    assert(h.payload_len == payload.size());

    const auto status = static_cast<linep::ResultStatus>(payload[0]);
    std::string parsed_text(payload.begin() + 1, payload.end());

    assert(status == linep::RESULT_OK);
    assert(parsed_text == "ok");

    std::puts("[PASS] test_result_parsing");
    return 0;
}
