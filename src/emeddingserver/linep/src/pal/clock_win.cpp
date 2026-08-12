#include "clock.hpp"
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

namespace linep::pal {

namespace {
    // Cache frequency to avoid repeated QueryPerformanceFrequency calls.
    inline LONGLONG qpf() noexcept {
        static LONGLONG freq = [] {
            LARGE_INTEGER f{};
            QueryPerformanceFrequency(&f);
            return f.QuadPart;
        }();
        return freq;
    }
    inline LONGLONG qpc() noexcept {
        LARGE_INTEGER c{};
        QueryPerformanceCounter(&c);
        return c.QuadPart;
    }
} // namespace

uint64_t clock_ms() noexcept {
    return static_cast<uint64_t>(qpc()) * 1'000ULL
         / static_cast<uint64_t>(qpf());
}

uint64_t clock_us() noexcept {
    return static_cast<uint64_t>(qpc()) * 1'000'000ULL
         / static_cast<uint64_t>(qpf());
}

void sleep_ms(uint32_t ms) noexcept {
    Sleep(static_cast<DWORD>(ms));
}

} // namespace linep::pal
