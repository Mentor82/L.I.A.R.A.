// Test: when Worker 1 is in cooldown (simulating a previous timeout),
//       the scheduler correctly falls back to Worker 2.
//
// This validates the retry semantic at the selection layer:
//   Dispatch attempt 1 → Worker 1 fails → timeout_count++ + cooldown set.
//   Dispatch attempt 2 → Worker 1 blocked by cooldown → Worker 2 selected.
#include "sched_helper.hpp"
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    using namespace linep::scheduler;
    using namespace std::chrono_literals;

    const auto now = std::chrono::steady_clock::now();

    // Simulate post-failure state for Worker 1.
    auto w1 = make_good_slot(1u, 0u);
    w1.timeout_count  = 1u;
    w1.cooldown_until = now + 60s;   // deep in cooldown

    auto w2 = make_good_slot(2u, 0u); // clean

    std::map<SlotKey, SlotState> slots;
    slots[{1, 0}] = w1;
    slots[{2, 0}] = w2;

    // Attempt 1 scenario: Worker 1 was selected before and failed.
    // Now: Worker 1 is in cooldown → select_best_slot skips it.
    assert(!is_eligible(w1, now));
    assert( is_eligible(w2, now));

    const auto best = select_best_slot(slots, linep::TASK_INSTRUCT, now);
    assert(best.has_value());
    assert(best->worker_id == 2u);  // retry lands on Worker 2

    std::puts("[PASS] test_scheduler_retries_on_second_worker");
    return 0;
}
