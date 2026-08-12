#include "socket.hpp"
#include <sys/socket.h>
#include <sys/select.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <cerrno>
#include <cstring>

namespace linep::pal {

void net_init()    noexcept {}
void net_cleanup() noexcept {}

// ── UDP ───────────────────────────────────────────────────────────────────────

Socket udp_open() noexcept {
    int s = ::socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP);
    if (s < 0) return {};
    return Socket{s};
}

bool udp_bind(Socket& s, uint16_t port) noexcept {
    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port        = htons(port);
    return ::bind(s.fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0;
}

void udp_set_recv_timeout(Socket& s, uint32_t ms) noexcept {
    timeval tv{};
    tv.tv_sec  = static_cast<time_t>(ms / 1000u);
    tv.tv_usec = static_cast<suseconds_t>((ms % 1000u) * 1000u);
    setsockopt(s.fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
}

int udp_sendto(Socket& s, const char* host, uint16_t port,
               const uint8_t* buf, int len) noexcept {
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(port);
    ::inet_pton(AF_INET, host, &addr.sin_addr);
    return static_cast<int>(
        ::sendto(s.fd, buf, static_cast<size_t>(len), 0,
                 reinterpret_cast<sockaddr*>(&addr), sizeof(addr)));
}

int udp_recvfrom(Socket& s, uint8_t* buf, int len,
                 char* src_ip, int ip_buf_len,
                 uint16_t* src_port) noexcept {
    sockaddr_in from{};
    socklen_t from_len = sizeof(from);
    int r = static_cast<int>(
        ::recvfrom(s.fd, buf, static_cast<size_t>(len), 0,
                   reinterpret_cast<sockaddr*>(&from), &from_len));
    if (r > 0) {
        if (src_ip)   ::inet_ntop(AF_INET, &from.sin_addr, src_ip, ip_buf_len);
        if (src_port) *src_port = ntohs(from.sin_port);
    }
    return r;
}

// ── TCP ───────────────────────────────────────────────────────────────────────

Socket tcp_connect(const char* host, uint16_t port,
                   uint32_t /*timeout_ms*/) noexcept {
    int s = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s < 0) return {};
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(port);
    ::inet_pton(AF_INET, host, &addr.sin_addr);
    if (::connect(s, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        ::close(s);
        return {};
    }
    return Socket{s};
}

Socket tcp_listen(uint16_t port, int backlog) noexcept {
    int s = ::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s < 0) return {};
    int opt = 1;
    ::setsockopt(s, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port        = htons(port);
    if (::bind(s, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0 ||
        ::listen(s, backlog) != 0) {
        ::close(s);
        return {};
    }
    return Socket{s};
}

Socket tcp_accept(Socket& server) noexcept {
    int c = ::accept(server.fd, nullptr, nullptr);
    if (c < 0) return {};
    return Socket{c};
}

int tcp_send_all(Socket& s, const uint8_t* buf, int len) noexcept {
    int sent = 0;
    while (sent < len) {
        int r = static_cast<int>(
            ::send(s.fd, buf + sent,
                   static_cast<size_t>(len - sent), MSG_NOSIGNAL));
        if (r <= 0) return r;
        sent += r;
    }
    return sent;
}

int tcp_recv_all(Socket& s, uint8_t* buf, int len) noexcept {
    int got = 0;
    while (got < len) {
        int r = static_cast<int>(
            ::recv(s.fd, buf + got, static_cast<size_t>(len - got), 0));
        if (r <= 0) return r;
        got += r;
    }
    return got;
}

void socket_close(Socket& s) noexcept {
    if (s.valid()) {
        ::close(s.fd);
        s.fd = INVALID_SOCK;
    }
}

} // namespace linep::pal
