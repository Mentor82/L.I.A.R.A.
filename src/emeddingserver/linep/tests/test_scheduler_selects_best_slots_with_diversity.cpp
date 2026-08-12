// Test: select_best_slots(k) returns up to k eligible slots, sorted by score,
// with worker diversity (max one slot per worker).
#include "sched_helper.hpp"
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    using namespace linep::scheduler;
    const auto now = std::chrono::steady_clock::now();

    std::map<SlotKey, SlotState> slots;

    // Worker 1 has two good slots; only one may be selected.
    slots[{1, 0}] = make_good_slot(1u, 0u, linep::TASK_INSTRUCT, 10u, 0u); // score 10
    slots[{1, 1}] = make_good_slot(1u, 1u, linep::TASK_INSTRUCT, 11u, 0u); // score 11

    // Other workers are eligible too.
    slots[{2, 0}] = make_good_slot(2u, 0u, linep::TASK_INSTRUCT, 20u, 0u); // score 20
    slots[{3, 0}] = make_good_slot(3u, 0u, linep::TASK_INSTRUCT, 30u, 0u); // score 30

    const auto best3 = select_best_slots(slots, linep::TASK_INSTRUCT, now, 3);
    assert(best3.size() == 3u);

    // First pick is the best scoring Worker 1 slot.
    assert(best3[0].worker_id == 1u);
    assert(best3[0].slot_id == 0u);

    // Remaining picks should be the next best workers, not Worker 1 again.
    assert(best3[1].worker_id == 2u);
    assert(best3[2].worker_id == 3u);
    assert(best3[1].worker_id != best3[2].worker_id);

    std::puts("[PASS] test_scheduler_selects_best_slots_with_diversity");
    return 0;
}
