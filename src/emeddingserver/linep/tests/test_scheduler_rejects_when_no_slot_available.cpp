// Test: select_best_slot returns nullopt when every registered slot is
//       ineligible. Tasks must not be dispatched to broken workers.
#include "sched_helper.hpp"
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    using namespace linep::scheduler;
    using namespace std::chrono_literals;

    const auto now = std::chrono::steady_clock::now();

    std::map<SlotKey, SlotState> slots;

    // No slots at all.
    assert(!select_best_slot(slots, linep::TASK_INSTRUCT, now).has_value());

    // Wrong task type only.
    slots[{1, 0}] = make_good_slot(1u, 0u, linep::TASK_CODE);
    assert(!select_best_slot(slots, linep::TASK_INSTRUCT, now).has_value());

    // Slot of correct type but all hard-filtered.
    auto overloaded = make_good_slot(2u, 0u, linep::TASK_INSTRUCT);
    overloaded.load = 95u;  // >= 90 → rejected
    slots[{2, 0}] = overloaded;
    assert(!select_best_slot(slots, linep::TASK_INSTRUCT, now).has_value());

    auto full_queue = make_good_slot(3u, 0u, linep::TASK_INSTRUCT);
    full_queue.queue_depth = 8u;  // >= 8 → rejected
    slots[{3, 0}] = full_queue;
    assert(!select_best_slot(slots, linep::TASK_INSTRUCT, now).has_value());

    auto in_cooldown = make_good_slot(4u, 0u, linep::TASK_INSTRUCT);
    in_cooldown.cooldown_until = now + 60s;
    slots[{4, 0}] = in_cooldown;
    assert(!select_best_slot(slots, linep::TASK_INSTRUCT, now).has_value());

    // Adding one healthy slot must make selection succeed.
    slots[{5, 0}] = make_good_slot(5u, 0u, linep::TASK_INSTRUCT);
    const auto best = select_best_slot(slots, linep::TASK_INSTRUCT, now);
    assert(best.has_value());
    assert(best->worker_id == 5u);

    std::puts("[PASS] test_scheduler_rejects_when_no_slot_available");
    return 0;
}
