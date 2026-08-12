#pragma once
#include <linep/export.h>
#include <linep/types.hpp>
#include <cstdint>

// ── Public DLL boundary — pure virtual interfaces only ───────────────────────
// No std::string / std::thread / std::atomic at the ABI boundary.
// Avoids MSVC C4251 and guarantees stable vtable across compiler versions.

namespace linep::udp {

// ── Heartbeat Sender ─────────────────────────────────────────────────────────
// Sends HeartbeatCompact frames periodically via UDP to a Scheduler.

class LINEP_API IHeartbeatSender {
public:
    virtual ~IHeartbeatSender() = default;

    // Start background thread. interval_ms: send period (default 1 s).
    virtual bool start(const char* target_ip, uint16_t target_port,
                       uint32_t interval_ms = 1000) = 0;

    // Stop background thread and release socket.
    virtual void stop() = 0;

    // Update slot state — safe to call from any thread at any time.
    virtual void set_status(uint8_t slot_flags,
                             uint8_t load,
                             uint8_t queue_depth) = 0;
};

// Factory — caller owns the object; destroy with destroy_heartbeat_sender().
LINEP_API IHeartbeatSender* create_heartbeat_sender(uint16_t worker_id,
                                                     uint8_t  slot_id);
LINEP_API void               destroy_heartbeat_sender(IHeartbeatSender* p);

// ── Heartbeat Receiver ────────────────────────────────────────────────────────
// Listens on a UDP port; calls Callback for every valid compact frame.

class LINEP_API IHeartbeatReceiver {
public:
    // Callback type — must be plain function pointer (no captures, no std::function)
    // to keep the DLL boundary clean.
    using Callback = void (*)(const linep::HeartbeatCompact& frame,
                               const char* src_ip,
                               uint16_t    src_port,
                               void*       user_data);

    virtual ~IHeartbeatReceiver() = default;

    virtual bool start(uint16_t port, Callback cb,
                       void* user_data = nullptr) = 0;
    virtual void stop() = 0;
};

LINEP_API IHeartbeatReceiver* create_heartbeat_receiver();
LINEP_API void                 destroy_heartbeat_receiver(IHeartbeatReceiver* p);

} // namespace linep::udp
