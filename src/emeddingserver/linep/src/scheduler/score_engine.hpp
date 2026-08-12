#pragma once
#include "slot_registry.hpp"
#include <chrono>
#include <map>
#include <optional>
#include <vector>

namespace linep::scheduler {

// Heartbeat not received within this window → slot is stale.
static constexpr auto HEARTBEAT_TIMEOUT = std::chrono::seconds(5);

// ── Eligibility filter ────────────────────────────────────────────────────────
// Returns false if the slot MUST NOT be selected (hard constraints).
bool is_eligible(const SlotState& slot,
                 std::chrono::steady_clock::time_point now) noexcept;

// ── Score function ────────────────────────────────────────────────────────────
// Lower score = preferred. Only call for eligible slots.
double score_slot(const SlotState& slot) noexcept;

// ── Tie-breaking ──────────────────────────────────────────────────────────────
// Returns true if a is strictly better than b when scores are equal.
bool is_better_tie(const SlotState& a, const SlotState& b) noexcept;

// ── Cooldown schedule ─────────────────────────────────────────────────────────
// Exponential back-off: 5 s / 30 s / 120 s
std::chrono::seconds cooldown_for(uint32_t failures) noexcept;

// ── Slot selection ────────────────────────────────────────────────────────────
// Returns the key of the best eligible slot for the given task type.
// Returns nullopt if no eligible slot exists.
std::optional<SlotKey> select_best_slot(
    const std::map<SlotKey, SlotState>& slots,
    linep::TaskType                      task_type,
    std::chrono::steady_clock::time_point now) noexcept;

// Returns up to k best eligible slots for task_type.
// Applies hard eligibility filters, score sorting, and worker diversity
// (max one slot per worker_id).
std::vector<SlotKey> select_best_slots(
    const std::map<SlotKey, SlotState>& slots,
    linep::TaskType                      task_type,
    std::chrono::steady_clock::time_point now,
    int                                  k) noexcept;

} // namespace linep::scheduler
