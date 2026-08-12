#pragma once

#include <cstdint>
#include <cstring>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace liara::common::wire_v2 {

constexpr std::uint16_t kMagic = 0x4C54; // 'LT'
constexpr std::uint8_t kVersion = 2;

enum class MessageType : std::uint8_t {
    TaskDispatch = 1,
    TaskAccepted = 2,
    TaskResult = 3,
    TaskError = 4,
    Heartbeat = 5,
};

struct Envelope {
    MessageType message_type = MessageType::TaskDispatch;
    std::uint16_t flags = 0;
    std::uint64_t task_id = 0;
    std::uint64_t correlation_id = 0;
    std::vector<std::uint8_t> payload;
};

struct TaskDispatchPayload {
    std::string task_type;
    std::string input;
    std::uint32_t max_tokens = 256;
    float temperature = 0.2F;
};

struct TaskResultPayload {
    bool success = false;
    std::uint32_t infer_ms = 0;
    std::string output;
    std::string error_message;
};

namespace detail {
inline void write_u16(std::vector<std::uint8_t>& out, std::uint16_t v) {
    out.push_back(static_cast<std::uint8_t>((v >> 8) & 0xFF));
    out.push_back(static_cast<std::uint8_t>(v & 0xFF));
}

inline void write_u32(std::vector<std::uint8_t>& out, std::uint32_t v) {
    out.push_back(static_cast<std::uint8_t>((v >> 24) & 0xFF));
    out.push_back(static_cast<std::uint8_t>((v >> 16) & 0xFF));
    out.push_back(static_cast<std::uint8_t>((v >> 8) & 0xFF));
    out.push_back(static_cast<std::uint8_t>(v & 0xFF));
}

inline void write_u64(std::vector<std::uint8_t>& out, std::uint64_t v) {
    for (int i = 7; i >= 0; --i) {
        out.push_back(static_cast<std::uint8_t>((v >> (8 * i)) & 0xFF));
    }
}

inline std::uint16_t read_u16(const std::vector<std::uint8_t>& in, std::size_t& off) {
    if (off + 2 > in.size()) throw std::runtime_error("short read u16");
    const std::uint16_t v = (static_cast<std::uint16_t>(in[off]) << 8)
                          | static_cast<std::uint16_t>(in[off + 1]);
    off += 2;
    return v;
}

inline std::uint32_t read_u32(const std::vector<std::uint8_t>& in, std::size_t& off) {
    if (off + 4 > in.size()) throw std::runtime_error("short read u32");
    const std::uint32_t v = (static_cast<std::uint32_t>(in[off]) << 24)
                          | (static_cast<std::uint32_t>(in[off + 1]) << 16)
                          | (static_cast<std::uint32_t>(in[off + 2]) << 8)
                          | static_cast<std::uint32_t>(in[off + 3]);
    off += 4;
    return v;
}

inline std::uint64_t read_u64(const std::vector<std::uint8_t>& in, std::size_t& off) {
    if (off + 8 > in.size()) throw std::runtime_error("short read u64");
    std::uint64_t v = 0;
    for (int i = 0; i < 8; ++i) {
        v = (v << 8) | static_cast<std::uint64_t>(in[off + i]);
    }
    off += 8;
    return v;
}

inline void write_str(std::vector<std::uint8_t>& out, const std::string& s) {
    if (s.size() > 0xFFFF) throw std::runtime_error("string too large");
    write_u16(out, static_cast<std::uint16_t>(s.size()));
    out.insert(out.end(), s.begin(), s.end());
}

inline std::string read_str(const std::vector<std::uint8_t>& in, std::size_t& off) {
    const std::uint16_t len = read_u16(in, off);
    if (off + len > in.size()) throw std::runtime_error("short read str");
    std::string s(reinterpret_cast<const char*>(&in[off]), len);
    off += len;
    return s;
}

inline std::uint32_t checksum32(const std::vector<std::uint8_t>& bytes) {
    // FNV-1a 32-bit checksum for quick corruption detection.
    std::uint32_t h = 2166136261u;
    for (const auto b : bytes) {
        h ^= static_cast<std::uint32_t>(b);
        h *= 16777619u;
    }
    return h;
}
} // namespace detail

inline std::vector<std::uint8_t> serialize_envelope(const Envelope& env) {
    std::vector<std::uint8_t> out;
    out.reserve(2 + 1 + 1 + 2 + 2 + 4 + 8 + 8 + 4 + env.payload.size());

    detail::write_u16(out, kMagic);
    out.push_back(kVersion);
    out.push_back(static_cast<std::uint8_t>(env.message_type));
    detail::write_u16(out, env.flags);
    detail::write_u16(out, 0); // reserved
    detail::write_u32(out, static_cast<std::uint32_t>(env.payload.size()));
    detail::write_u64(out, env.task_id);
    detail::write_u64(out, env.correlation_id);
    detail::write_u32(out, detail::checksum32(env.payload));
    out.insert(out.end(), env.payload.begin(), env.payload.end());
    return out;
}

inline std::optional<Envelope> parse_envelope(const std::vector<std::uint8_t>& frame) {
    try {
        std::size_t off = 0;
        if (detail::read_u16(frame, off) != kMagic) return std::nullopt;
        if (frame.at(off++) != kVersion) return std::nullopt;
        const auto mt = static_cast<MessageType>(frame.at(off++));
        const std::uint16_t flags = detail::read_u16(frame, off);
        (void)detail::read_u16(frame, off); // reserved
        const std::uint32_t payload_len = detail::read_u32(frame, off);
        const std::uint64_t task_id = detail::read_u64(frame, off);
        const std::uint64_t correlation_id = detail::read_u64(frame, off);
        const std::uint32_t checksum = detail::read_u32(frame, off);

        if (off + payload_len != frame.size()) return std::nullopt;
        std::vector<std::uint8_t> payload(frame.begin() + static_cast<long long>(off), frame.end());
        if (detail::checksum32(payload) != checksum) return std::nullopt;

        Envelope out;
        out.message_type = mt;
        out.flags = flags;
        out.task_id = task_id;
        out.correlation_id = correlation_id;
        out.payload = std::move(payload);
        return out;
    } catch (...) {
        return std::nullopt;
    }
}

inline std::vector<std::uint8_t> serialize_task_dispatch(const TaskDispatchPayload& p) {
    std::vector<std::uint8_t> out;
    detail::write_u32(out, p.max_tokens);

    std::uint32_t temp_bits = 0;
    std::memcpy(&temp_bits, &p.temperature, sizeof(temp_bits));
    detail::write_u32(out, temp_bits);

    detail::write_str(out, p.task_type);
    detail::write_str(out, p.input);
    return out;
}

inline std::optional<TaskDispatchPayload> parse_task_dispatch(const std::vector<std::uint8_t>& data) {
    try {
        std::size_t off = 0;
        TaskDispatchPayload p;
        p.max_tokens = detail::read_u32(data, off);

        const std::uint32_t temp_bits = detail::read_u32(data, off);
        std::memcpy(&p.temperature, &temp_bits, sizeof(temp_bits));

        p.task_type = detail::read_str(data, off);
        p.input = detail::read_str(data, off);
        if (off != data.size()) return std::nullopt;
        return p;
    } catch (...) {
        return std::nullopt;
    }
}

inline std::vector<std::uint8_t> serialize_task_result(const TaskResultPayload& p) {
    std::vector<std::uint8_t> out;
    out.push_back(static_cast<std::uint8_t>(p.success ? 1 : 0));
    detail::write_u32(out, p.infer_ms);
    detail::write_str(out, p.output);
    detail::write_str(out, p.error_message);
    return out;
}

inline std::optional<TaskResultPayload> parse_task_result(const std::vector<std::uint8_t>& data) {
    try {
        std::size_t off = 0;
        TaskResultPayload p;
        if (off >= data.size()) return std::nullopt;
        p.success = data[off++] != 0;
        p.infer_ms = detail::read_u32(data, off);
        p.output = detail::read_str(data, off);
        p.error_message = detail::read_str(data, off);
        if (off != data.size()) return std::nullopt;
        return p;
    } catch (...) {
        return std::nullopt;
    }
}

} // namespace liara::common::wire_v2
