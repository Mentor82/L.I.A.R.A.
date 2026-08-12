// Test: a slot with error=true is excluded by the hard filter.
#include "sched_helper.hpp"
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    using namespace linep::scheduler;
    const auto now = std::chrono::steady_clock::now();

    std::map<SlotKey, SlotState> slots;
    auto s = make_good_slot(1u, 0u);
    s.error = true;
    slots[{1, 0}] = s;

    assert(!is_eligible(s, now));
    assert(!select_best_slot(slots, linep::TASK_INSTRUCT, now).has_value());

    // A second, healthy slot must still win.
    slots[{2, 0}] = make_good_slot(2u, 0u);
    const auto best = select_best_slot(slots, linep::TASK_INSTRUCT, now);
    assert(best.has_value());
    assert(best->worker_id == 2u);

    std::puts("[PASS] test_scheduler_ignores_error_slot");
    return 0;
}
