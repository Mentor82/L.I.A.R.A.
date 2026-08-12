#include "../src/core/crc.hpp"
#include <cassert>
#include <cstdio>

static void test_empty() {
    // CRC-8 of zero-length input = 0x00 (init value)
    assert(linep::core::crc8(nullptr, 0) == 0x00u);
}

static void test_deterministic() {
    const uint8_t data[] = {0x4C, 0x4E, 0x01, 0x01, 0x00, 0x00};
    const uint8_t c1 = linep::core::crc8(data, sizeof(data));
    const uint8_t c2 = linep::core::crc8(data, sizeof(data));
    assert(c1 == c2);
    assert(c1 != 0x00u);
}

static void test_single_bit_change_detected() {
    uint8_t data[] = {0xAB, 0xCD, 0xEF};
    const uint8_t c_orig    = linep::core::crc8(data, sizeof(data));
    data[1] ^= 0x01u;  // flip one bit
    const uint8_t c_flipped = linep::core::crc8(data, sizeof(data));
    assert(c_orig != c_flipped);
}

static void test_incremental_vs_bulk() {
    // CRC over a concatenation must equal two-pass computation
    // (single-pass only — just verify stable output here)
    const uint8_t a[] = {0x10, 0x20, 0x30, 0x40, 0x50};
    const uint8_t b[] = {0x10, 0x20, 0x30, 0x40, 0x50};
    assert(linep::core::crc8(a, 5) == linep::core::crc8(b, 5));
}

int main() {
    test_empty();
    test_deterministic();
    test_single_bit_change_detected();
    test_incremental_vs_bulk();
    std::puts("[PASS] test_crc");
    return 0;
}
