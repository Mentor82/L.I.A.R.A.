#pragma once
// Shared helpers for scheduler unit tests.
// All tests are pure in-memory — no sockets, no threads.
#include "../src/scheduler/slot_registry.hpp"
#include "../src/scheduler/score_engine.hpp"
#include <linep/messages.hpp>
#include <chrono>

// Build a fully eligible SlotState with a fresh heartbeat timestamp.
inline linep::scheduler::SlotState make_good_slot(
    uint16_t        worker_id = 1u,
    uint8_t         slot_id   = 0u,
    linep::TaskType type      = linep::TASK_INSTRUCT,
    uint8_t         load      = 20u,
    uint8_t         queue     = 0u)
{
    linep::scheduler::SlotState s;
    s.worker_id      = worker_id;
    s.slot_id        = slot_id;
    s.type           = type;
    s.alive          = true;
    s.ready          = true;
    s.load           = load;
    s.queue_depth    = queue;
    s.last_heartbeat = std::chrono::steady_clock::now();
    return s;
}
