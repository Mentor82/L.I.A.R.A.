// Test: a slot whose last heartbeat is older than HEARTBEAT_TIMEOUT is stale
//       and must not be selected.
#include "sched_helper.hpp"
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    using namespace linep::scheduler;
    const auto now = std::chrono::steady_clock::now();

    std::map<SlotKey, SlotState> slots;
    auto s = make_good_slot(1u, 0u);
    // Backdate the heartbeat past the timeout window.
    s.last_heartbeat = now - std::chrono::seconds(10);
    slots[{1, 0}] = s;

    assert(!is_eligible(s, now));
    assert(!select_best_slot(slots, linep::TASK_INSTRUCT, now).has_value());

    // After applying a fresh heartbeat the slot becomes eligible again.
    s.last_heartbeat = now;
    slots[{1, 0}] = s;
    assert(is_eligible(s, now));
    assert(select_best_slot(slots, linep::TASK_INSTRUCT, now).has_value());

    std::puts("[PASS] test_scheduler_ignores_stale_slot");
    return 0;
}
