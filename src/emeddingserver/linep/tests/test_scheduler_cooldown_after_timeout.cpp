// Test: after a task timeout the scheduler applies a cooldown to the slot.
// Verify:
//   1. cooldown_for() returns the correct durations (5s / 30s / 120s).
//   2. A slot in active cooldown is not eligible.
//   3. Once the cooldown expires the slot becomes eligible again.
#include "sched_helper.hpp"
#include <cassert>
#include <cstdio>
#include <map>

int main() {
    using namespace linep::scheduler;
    using namespace std::chrono_literals;

    // ── cooldown_for() schedule ───────────────────────────────────────────────
    assert(cooldown_for(0u) == 5s);   // first failure  → 5 s
    assert(cooldown_for(1u) == 5s);
    assert(cooldown_for(2u) == 30s);  // second failure → 30 s
    assert(cooldown_for(3u) == 120s); // third+         → 120 s
    assert(cooldown_for(9u) == 120s);

    const auto now = std::chrono::steady_clock::now();

    // ── Slot enters cooldown after a timeout ──────────────────────────────────
    auto s = make_good_slot(1u, 0u);
    s.timeout_count  = 1u;
    s.cooldown_until = now + cooldown_for(1u);  // now + 5 s

    assert(!is_eligible(s, now));  // in cooldown → blocked

    // ── Cooldown just expired ─────────────────────────────────────────────────
    s.cooldown_until = now - 1s;   // 1 s in the past
    assert(is_eligible(s, now));   // eligible again

    // ── Multiple failures ramp up the penalty ─────────────────────────────────
    std::map<SlotKey, SlotState> slots;
    auto bad = make_good_slot(1u, 0u);
    bad.timeout_count  = 3u;
    bad.cooldown_until = now + cooldown_for(3u);  // now + 120 s
    slots[{1, 0}] = bad;

    assert(!select_best_slot(slots, linep::TASK_INSTRUCT, now).has_value());

    // Good slot wins when bad one is in cooldown.
    slots[{2, 0}] = make_good_slot(2u, 0u);
    const auto best = select_best_slot(slots, linep::TASK_INSTRUCT, now);
    assert(best.has_value());
    assert(best->worker_id == 2u);

    std::puts("[PASS] test_scheduler_cooldown_after_timeout");
    return 0;
}
