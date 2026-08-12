#pragma once
#include <linep/messages.hpp>
#include <chrono>
#include <cstdint>
#include <vector>
#include "slot_registry.hpp"

namespace linep::scheduler {

// Plain function pointer — keeps DLL boundary clean (no std::function).
using ResultCallback = void (*)(
    uint32_t              correlation_id,
    linep::ResultStatus   status,
    const uint8_t*        payload,
    uint32_t              payload_len,
    void*                 user_data);

struct PendingTask {
    uint32_t             correlation_id{0};
    linep::TaskType      type{linep::TASK_INSTRUCT};
    std::vector<uint8_t> payload;
    uint32_t             timeout_ms{5000u};
    uint32_t             max_attempts{3u};
    uint32_t             attempt_count{0u};
    ResultCallback       callback{nullptr};
    void*                user_data{nullptr};
};

struct ActiveTask {
    PendingTask                           task;
    SlotKey                               assigned_slot;
    std::chrono::steady_clock::time_point started_at{};
};

} // namespace linep::scheduler
