// Test: queue_depth contributes 10× to the score, so a slot with a longer
//       queue loses even if its CPU load is much lower.
//   Worker 1 / load 20 / queue 0  → score = 20
//   Worker 2 / load  5 / queue 3  → score = 5 + 30 = 35
// Expected: Worker 1 wins (lower score).
#include "sched_helper.hpp"
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    using namespace linep::scheduler;
    const auto now = std::chrono::steady_clock::now();

    std::map<SlotKey, SlotState> slots;
    slots[{1, 0}] = make_good_slot(1u, 0u, linep::TASK_INSTRUCT, 20u, 0u);
    slots[{2, 0}] = make_good_slot(2u, 0u, linep::TASK_INSTRUCT,  5u, 3u);

    assert(score_slot(slots[{1,0}]) < score_slot(slots[{2,0}]));

    const auto best = select_best_slot(slots, linep::TASK_INSTRUCT, now);
    assert(best.has_value());
    assert(best->worker_id == 1u);

    std::puts("[PASS] test_scheduler_prefers_lower_queue");
    return 0;
}
