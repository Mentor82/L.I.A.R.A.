#pragma once

#include "crc8.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace liara::common {

constexpr std::size_t kHeartbeatPacketSize = 12;
constexpr std::uint16_t kHeartbeatMagic = 0x4C48; // 'LH'
constexpr std::uint8_t kHeartbeatVersion = 1;
constexpr std::uint8_t kHeartbeatType = 0x01;

enum class HeartbeatParseError : std::uint8_t {
    None = 0,
    InvalidMagic,
    InvalidVersion,
    InvalidType,
    InvalidCrc,
};

struct HeartbeatPacket {
    std::uint16_t magic = kHeartbeatMagic;
    std::uint8_t version = kHeartbeatVersion;
    std::uint8_t type = kHeartbeatType;
    std::uint16_t worker_id = 0;
    std::uint8_t slot_id = 0;
    std::uint8_t flags = 0;
    std::uint8_t load = 0;
    std::uint8_t queue = 0;
    std::uint8_t seq = 0;
    std::uint8_t crc8 = 0;
};

inline std::array<std::uint8_t, kHeartbeatPacketSize> serialize_heartbeat(const HeartbeatPacket& packet) {
    std::array<std::uint8_t, kHeartbeatPacketSize> bytes{};

    bytes[0] = static_cast<std::uint8_t>(packet.magic >> 8U);
    bytes[1] = static_cast<std::uint8_t>(packet.magic & 0xFFU);
    bytes[2] = packet.version;
    bytes[3] = packet.type;
    bytes[4] = static_cast<std::uint8_t>(packet.worker_id >> 8U);
    bytes[5] = static_cast<std::uint8_t>(packet.worker_id & 0xFFU);
    bytes[6] = packet.slot_id;
    bytes[7] = packet.flags;
    bytes[8] = packet.load;
    bytes[9] = packet.queue;
    bytes[10] = packet.seq;
    bytes[11] = crc8(bytes, kHeartbeatPacketSize - 1);

    return bytes;
}

inline HeartbeatParseError deserialize_heartbeat(
    const std::array<std::uint8_t, kHeartbeatPacketSize>& bytes,
    HeartbeatPacket& out_packet) {
    const std::uint16_t magic =
        static_cast<std::uint16_t>((static_cast<std::uint16_t>(bytes[0]) << 8U) | bytes[1]);
    if (magic != kHeartbeatMagic) {
        return HeartbeatParseError::InvalidMagic;
    }
    if (bytes[2] != kHeartbeatVersion) {
        return HeartbeatParseError::InvalidVersion;
    }
    if (bytes[3] != kHeartbeatType) {
        return HeartbeatParseError::InvalidType;
    }

    const std::uint8_t expected_crc = crc8(bytes, kHeartbeatPacketSize - 1);
    if (expected_crc != bytes[11]) {
        return HeartbeatParseError::InvalidCrc;
    }

    out_packet.magic = magic;
    out_packet.version = bytes[2];
    out_packet.type = bytes[3];
    out_packet.worker_id =
        static_cast<std::uint16_t>((static_cast<std::uint16_t>(bytes[4]) << 8U) | bytes[5]);
    out_packet.slot_id = bytes[6];
    out_packet.flags = bytes[7];
    out_packet.load = bytes[8];
    out_packet.queue = bytes[9];
    out_packet.seq = bytes[10];
    out_packet.crc8 = bytes[11];

    return HeartbeatParseError::None;
}

} // namespace liara::common
