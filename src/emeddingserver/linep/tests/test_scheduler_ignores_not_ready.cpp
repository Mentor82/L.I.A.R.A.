// Test: a slot with ready=false is excluded by the hard filter.
#include "sched_helper.hpp"
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    using namespace linep::scheduler;
    const auto now = std::chrono::steady_clock::now();

    std::map<SlotKey, SlotState> slots;
    auto s = make_good_slot(1u, 0u);
    s.ready = false;          // hard filter must catch this
    slots[{1, 0}] = s;

    assert(!is_eligible(s, now));

    const auto best = select_best_slot(slots, linep::TASK_INSTRUCT, now);
    assert(!best.has_value());

    std::puts("[PASS] test_scheduler_ignores_not_ready");
    return 0;
}
