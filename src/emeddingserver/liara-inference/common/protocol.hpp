#pragma once

#include "heartbeat.hpp"
#include "helper_contract.hpp"
#include "task_contract.hpp"

#include <cstdint>

namespace liara::common {

enum class MessageType : std::uint8_t {
    Heartbeat = 1,
    TaskRequest = 2,
    TaskResult = 3,
};

struct WireHeader {
    std::uint16_t magic = 0x4C49; // 'LI'
    std::uint8_t version = 1;
    MessageType type = MessageType::Heartbeat;
    std::uint32_t payload_size = 0;
};

} // namespace liara::common
