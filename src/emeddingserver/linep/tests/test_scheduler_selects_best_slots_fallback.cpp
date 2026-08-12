// Test: select_best_slots(k) falls back to 2 or 1 when fewer eligible workers
// are available than requested.
#include "sched_helper.hpp"
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    using namespace linep::scheduler;
    const auto now = std::chrono::steady_clock::now();

    std::map<SlotKey, SlotState> slots;
    slots[{10, 0}] = make_good_slot(10u, 0u, linep::TASK_CODE, 10u, 0u);
    slots[{20, 0}] = make_good_slot(20u, 0u, linep::TASK_CODE, 20u, 0u);

    const auto best2 = select_best_slots(slots, linep::TASK_CODE, now, 3);
    assert(best2.size() == 2u);

    // Only one eligible worker left.
    auto overloaded = make_good_slot(20u, 0u, linep::TASK_CODE, 95u, 0u);
    slots[{20, 0}] = overloaded;

    const auto best1 = select_best_slots(slots, linep::TASK_CODE, now, 3);
    assert(best1.size() == 1u);
    assert(best1[0].worker_id == 10u);

    // No eligible worker left.
    auto overloaded2 = make_good_slot(10u, 0u, linep::TASK_CODE, 95u, 0u);
    slots[{10, 0}] = overloaded2;

    const auto best0 = select_best_slots(slots, linep::TASK_CODE, now, 3);
    assert(best0.empty());

    std::puts("[PASS] test_scheduler_selects_best_slots_fallback");
    return 0;
}
