#include "socket.hpp"
#define WIN32_LEAN_AND_MEAN
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")

// Verify our opaque handle is wide enough to hold a SOCKET.
static_assert(sizeof(linep::pal::RawSocket) >= sizeof(SOCKET),
              "RawSocket must be at least as wide as SOCKET");

namespace linep::pal {

namespace {
    inline SOCKET  to_sock  (RawSocket r) noexcept { return static_cast<SOCKET>(r);    }
    inline RawSocket from_sock(SOCKET s)  noexcept { return static_cast<RawSocket>(s); }
} // namespace

void net_init() noexcept {
    WSADATA wsa{};
    WSAStartup(MAKEWORD(2, 2), &wsa);
}

void net_cleanup() noexcept { WSACleanup(); }

// ── UDP ───────────────────────────────────────────────────────────────────────

Socket udp_open() noexcept {
    SOCKET s = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s == INVALID_SOCKET) return {};
    return Socket{from_sock(s)};
}

bool udp_bind(Socket& s, uint16_t port) noexcept {
    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port        = htons(port);
    return ::bind(to_sock(s.fd),
                  reinterpret_cast<sockaddr*>(&addr),
                  sizeof(addr)) == 0;
}

void udp_set_recv_timeout(Socket& s, uint32_t ms) noexcept {
    DWORD tv = static_cast<DWORD>(ms);
    setsockopt(to_sock(s.fd), SOL_SOCKET, SO_RCVTIMEO,
               reinterpret_cast<const char*>(&tv), sizeof(tv));
}

int udp_sendto(Socket& s, const char* host, uint16_t port,
               const uint8_t* buf, int len) noexcept {
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(port);
    inet_pton(AF_INET, host, &addr.sin_addr);
    return static_cast<int>(
        ::sendto(to_sock(s.fd),
                 reinterpret_cast<const char*>(buf), len, 0,
                 reinterpret_cast<sockaddr*>(&addr),
                 sizeof(addr)));
}

int udp_recvfrom(Socket& s, uint8_t* buf, int len,
                 char* src_ip, int ip_buf_len,
                 uint16_t* src_port) noexcept {
    sockaddr_in from{};
    int from_len = sizeof(from);
    int r = static_cast<int>(
        ::recvfrom(to_sock(s.fd),
                   reinterpret_cast<char*>(buf), len, 0,
                   reinterpret_cast<sockaddr*>(&from),
                   &from_len));
    if (r > 0) {
        if (src_ip)   inet_ntop(AF_INET, &from.sin_addr, src_ip, ip_buf_len);
        if (src_port) *src_port = ntohs(from.sin_port);
    }
    return r;
}

// ── TCP ───────────────────────────────────────────────────────────────────────

Socket tcp_connect(const char* host, uint16_t port,
                   uint32_t /*timeout_ms*/) noexcept {
    SOCKET s = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) return {};
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(port);
    inet_pton(AF_INET, host, &addr.sin_addr);
    if (::connect(s, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        closesocket(s);
        return {};
    }
    return Socket{from_sock(s)};
}

Socket tcp_listen(uint16_t port, int backlog) noexcept {
    SOCKET s = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) return {};
    BOOL opt = TRUE;
    setsockopt(s, SOL_SOCKET, SO_REUSEADDR,
               reinterpret_cast<const char*>(&opt), sizeof(opt));
    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port        = htons(port);
    if (::bind(s, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0 ||
        ::listen(s, backlog) != 0) {
        closesocket(s);
        return {};
    }
    return Socket{from_sock(s)};
}

Socket tcp_accept(Socket& server) noexcept {
    SOCKET c = ::accept(to_sock(server.fd), nullptr, nullptr);
    if (c == INVALID_SOCKET) return {};
    return Socket{from_sock(c)};
}

int tcp_send_all(Socket& s, const uint8_t* buf, int len) noexcept {
    int sent = 0;
    while (sent < len) {
        int r = static_cast<int>(
            ::send(to_sock(s.fd),
                   reinterpret_cast<const char*>(buf + sent),
                   len - sent, 0));
        if (r <= 0) return r;
        sent += r;
    }
    return sent;
}

int tcp_recv_all(Socket& s, uint8_t* buf, int len) noexcept {
    int got = 0;
    while (got < len) {
        int r = static_cast<int>(
            ::recv(to_sock(s.fd),
                   reinterpret_cast<char*>(buf + got),
                   len - got, 0));
        if (r <= 0) return r;
        got += r;
    }
    return got;
}

void socket_close(Socket& s) noexcept {
    if (s.valid()) {
        closesocket(to_sock(s.fd));
        s.fd = INVALID_SOCK;
    }
}

} // namespace linep::pal
