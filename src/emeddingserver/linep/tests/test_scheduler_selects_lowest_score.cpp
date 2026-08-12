// Test: select_best_slot picks the slot with the lowest composite score.
// Setup mirrors the spec §7 Multi-Worker-Auswahl example:
//   Worker 1 / load 20 / queue 1  → score = 20 + 10 = 30
//   Worker 2 / load 60 / queue 0  → score = 60 + 0  = 60
//   Worker 3 / load 10 / queue 4  → score = 10 + 40 = 50
// Expected: Worker 1 wins.
#include "sched_helper.hpp"
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    using namespace linep::scheduler;
    const auto now = std::chrono::steady_clock::now();

    std::map<SlotKey, SlotState> slots;
    slots[{1, 0}] = make_good_slot(1u, 0u, linep::TASK_INSTRUCT, 20u, 1u);
    slots[{2, 0}] = make_good_slot(2u, 0u, linep::TASK_INSTRUCT, 60u, 0u);
    slots[{3, 0}] = make_good_slot(3u, 0u, linep::TASK_INSTRUCT, 10u, 4u);

    const auto best = select_best_slot(slots, linep::TASK_INSTRUCT, now);

    assert(best.has_value());
    assert(best->worker_id == 1u);
    assert(best->slot_id   == 0u);

    std::puts("[PASS] test_scheduler_selects_lowest_score");
    return 0;
}
