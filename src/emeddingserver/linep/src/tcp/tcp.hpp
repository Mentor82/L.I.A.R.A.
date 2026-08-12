#pragma once
#include <linep/export.h>
#include <linep/messages.hpp>
#include <linep/types.hpp>
#include <cstdint>

// ── Public DLL boundary — pure virtual interfaces only ───────────────────────
// No std::string / std::thread / std::atomic at the ABI boundary.

namespace linep::tcp {

// ── Task Sender ───────────────────────────────────────────────────────────────
// Client-side: connects to a worker, sends one TASK frame, waits for RESULT.
// Opens a fresh TCP connection per call (stateless, scheduler-friendly).

class LINEP_API ITcpTaskSender {
public:
    virtual ~ITcpTaskSender() = default;

    // Send a TASK frame and block until a RESULT arrives or timeout.
    //
    // Returns the ResultStatus byte (linep::ResultStatus):
    //   RESULT_OK (0x00) on success.
    //   RESULT_TIMEOUT    if no answer arrived within timeout_ms.
    //   RESULT_REJECTED   if the worker refused the task.
    //   RESULT_MODEL_ERROR / RESULT_INVALID_INPUT for worker-side errors.
    //
    // result_buf   : caller-allocated output buffer.
    // result_len   : [in]  capacity of result_buf in bytes.
    //                [out] actual bytes written on RESULT_OK.
    //
    // task_type    : one of linep::TaskType (TASK_INSTRUCT, TASK_CODE, …).
    // correlation_id: caller-chosen request ID; echoed in the RESULT header.
    virtual uint8_t send_task(const char*    host,
                               uint16_t       port,
                               uint8_t        task_type,
                               uint32_t       correlation_id,
                               uint16_t       worker_id,
                               uint8_t        slot_id,
                               const uint8_t* payload,
                               uint32_t       payload_len,
                               uint8_t*       result_buf,
                               uint32_t*      result_len,
                               uint32_t       timeout_ms = 5000) = 0;
};

LINEP_API ITcpTaskSender* create_task_sender();
LINEP_API void             destroy_task_sender(ITcpTaskSender* p);


// ── Task Receiver ─────────────────────────────────────────────────────────────
// Server-side: listens on a TCP port, accepts connections, dispatches TASKs
// to a callback, and sends back RESULT frames.
//
// One background thread per accepted connection → concurrent task handling.

class LINEP_API ITcpTaskReceiver {
public:
    // Called once per TASK frame received.
    //
    // task_type / correlation_id / worker_id / slot_id: from the TASK header.
    // payload / payload_len: task body bytes.
    //
    // result_buf / result_cap / result_len:
    //   Write the response body into result_buf (up to result_cap bytes).
    //   Set *result_len to the number of bytes written.
    //
    // Returns: linep::ResultStatus byte sent back to the client.
    using TaskCallback = uint8_t (*)(uint8_t         task_type,
                                      uint32_t        correlation_id,
                                      uint16_t        worker_id,
                                      uint8_t         slot_id,
                                      const uint8_t*  payload,
                                      uint32_t        payload_len,
                                      uint8_t*        result_buf,
                                      uint32_t        result_cap,
                                      uint32_t*       result_len,
                                      void*           user_data);

    virtual ~ITcpTaskReceiver() = default;

    // Start listening.  Returns false if the port cannot be bound.
    virtual bool start(uint16_t port, TaskCallback cb,
                       void* user_data = nullptr) = 0;

    // Stop accepting new connections and wait for active handlers to finish.
    virtual void stop() = 0;
};

LINEP_API ITcpTaskReceiver* create_task_receiver();
LINEP_API void               destroy_task_receiver(ITcpTaskReceiver* p);

} // namespace linep::tcp
