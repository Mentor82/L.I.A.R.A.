#pragma once
#include <cstdint>

namespace linep::pal {

// ── Opaque socket handle ─────────────────────────────────────────────────────
#ifdef _WIN32
    using RawSocket = uintptr_t;                        // matches SOCKET (UINT_PTR)
    static constexpr RawSocket INVALID_SOCK = ~static_cast<uintptr_t>(0u);
#else
    using RawSocket = int;
    static constexpr RawSocket INVALID_SOCK = -1;
#endif

struct Socket {
    RawSocket fd{INVALID_SOCK};
    [[nodiscard]] bool valid() const noexcept { return fd != INVALID_SOCK; }
};

// ── Lifecycle ─────────────────────────────────────────────────────────────────
// Must be called once before any socket operation (WSAStartup on Windows).
void net_init()    noexcept;
void net_cleanup() noexcept;

// ── UDP ───────────────────────────────────────────────────────────────────────
Socket udp_open()  noexcept;
bool   udp_bind(Socket& s, uint16_t port)              noexcept;
void   udp_set_recv_timeout(Socket& s, uint32_t ms)    noexcept;

int    udp_sendto(Socket& s, const char* host, uint16_t port,
                  const uint8_t* buf, int len)          noexcept;

// Returns bytes received, 0 = timeout, <0 = error.
// src_ip / src_port may be nullptr if not needed.
int    udp_recvfrom(Socket& s, uint8_t* buf, int len,
                    char* src_ip, int ip_buf_len,
                    uint16_t* src_port)                 noexcept;

// ── TCP ───────────────────────────────────────────────────────────────────────
Socket tcp_connect(const char* host, uint16_t port,
                   uint32_t timeout_ms = 5000)          noexcept;
Socket tcp_listen (uint16_t port, int backlog = 16)     noexcept;
Socket tcp_accept (Socket& server)                      noexcept;

// Returns bytes sent/recv, <=0 on error / connection closed.
int    tcp_send_all(Socket& s, const uint8_t* buf, int len) noexcept;
int    tcp_recv_all(Socket& s,       uint8_t* buf, int len) noexcept;

// ── Shared ────────────────────────────────────────────────────────────────────
void socket_close(Socket& s) noexcept;

} // namespace linep::pal
