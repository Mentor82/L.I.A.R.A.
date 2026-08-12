#pragma once
#include <cstdint>

namespace linep::pal {

// Monotonic clock — milliseconds
uint64_t clock_ms() noexcept;

// Monotonic clock — microseconds
uint64_t clock_us() noexcept;

// Sleep (best-effort, may overshoot slightly)
void sleep_ms(uint32_t ms) noexcept;

} // namespace linep::pal
