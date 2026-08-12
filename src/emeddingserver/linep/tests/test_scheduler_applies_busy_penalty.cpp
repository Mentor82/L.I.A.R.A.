// Test: busy flag adds +20 to the score.
//   Worker 1 / load 20 / not busy → score = 20
//   Worker 2 / load  5 / busy     → score = 5 + 20 = 25
// Expected: Worker 1 wins despite higher raw load, because Worker 2's busy
//           penalty is large enough to push its score above Worker 1.
//   Worker 1 / load 20 → score 20   <   Worker 2 / load 5 + busy 20 = 25
#include "sched_helper.hpp"
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    using namespace linep::scheduler;
    const auto now = std::chrono::steady_clock::now();

    auto s1 = make_good_slot(1u, 0u, linep::TASK_INSTRUCT, 20u, 0u);
    s1.busy = false;

    auto s2 = make_good_slot(2u, 0u, linep::TASK_INSTRUCT,  5u, 0u);
    s2.busy = true;   // +20

    // Verify the score formula.
    assert(score_slot(s1) == 20.0);
    assert(score_slot(s2) == 25.0);

    std::map<SlotKey, SlotState> slots;
    slots[{1, 0}] = s1;
    slots[{2, 0}] = s2;

    const auto best = select_best_slot(slots, linep::TASK_INSTRUCT, now);
    assert(best.has_value());
    assert(best->worker_id == 1u);

    std::puts("[PASS] test_scheduler_applies_busy_penalty");
    return 0;
}
