#include "clock.hpp"
#include <time.h>
#include <unistd.h>

namespace linep::pal {

namespace {
    inline uint64_t gettime_ns() noexcept {
        struct timespec ts{};
        clock_gettime(CLOCK_MONOTONIC, &ts);
        return static_cast<uint64_t>(ts.tv_sec)  * 1'000'000'000ULL
             + static_cast<uint64_t>(ts.tv_nsec);
    }
} // namespace

uint64_t clock_ms() noexcept { return gettime_ns() / 1'000'000ULL; }
uint64_t clock_us() noexcept { return gettime_ns() / 1'000ULL;     }

void sleep_ms(uint32_t ms) noexcept {
    usleep(static_cast<useconds_t>(ms) * 1'000u);
}

} // namespace linep::pal
