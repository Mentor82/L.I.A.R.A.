#include "score_engine.hpp"
#include <algorithm>
#include <limits>
#include <set>

namespace linep::scheduler {

bool is_eligible(const SlotState& slot,
                 std::chrono::steady_clock::time_point now) noexcept
{
    if (!slot.alive)        return false;
    if (!slot.ready)        return false;
    if (slot.error)         return false;
    if (slot.model_loading) return false;
    if (slot.thermal_limit) return false;
    if (slot.load >= 90u)   return false;
    if (slot.queue_depth >= 8u) return false;
    // Slot that has never sent a heartbeat is not eligible.
    if (slot.last_heartbeat == std::chrono::steady_clock::time_point{}) return false;
    if (now > slot.last_heartbeat + HEARTBEAT_TIMEOUT) return false;
    if (now < slot.cooldown_until) return false;
    return true;
}

double score_slot(const SlotState& slot) noexcept
{
    double s = 0.0;
    s += slot.load           * 1.0;
    s += slot.queue_depth    * 10.0;
    s += slot.avg_latency_ms * 0.02;
    if (slot.busy)          s += 20.0;
    if (slot.degraded)      s += 50.0;
    if (slot.thermal_limit) s += 100.0;
    s += slot.timeout_count * 15.0;
    s += slot.error_count   * 25.0;
    return s;
}

bool is_better_tie(const SlotState& a, const SlotState& b) noexcept
{
    if (a.queue_depth != b.queue_depth)
        return a.queue_depth < b.queue_depth;
    if (a.avg_latency_ms != b.avg_latency_ms)
        return a.avg_latency_ms < b.avg_latency_ms;
    // Least-recently-used wins (promotes fairness).
    return a.last_used < b.last_used;
}

std::chrono::seconds cooldown_for(uint32_t failures) noexcept
{
    using namespace std::chrono_literals;
    if (failures <= 1u) return  5s;
    if (failures == 2u) return 30s;
    return 120s;
}

std::optional<SlotKey> select_best_slot(
    const std::map<SlotKey, SlotState>& slots,
    linep::TaskType                      task_type,
    std::chrono::steady_clock::time_point now) noexcept
{
    const auto picks = select_best_slots(slots, task_type, now, 1);
    if (picks.empty()) return std::nullopt;
    return picks.front();
}

std::vector<SlotKey> select_best_slots(
    const std::map<SlotKey, SlotState>& slots,
    linep::TaskType                      task_type,
    std::chrono::steady_clock::time_point now,
    int                                  k) noexcept
{
    if (k <= 0) return {};

    struct Candidate {
        SlotKey key;
        const SlotState* slot;
        double score;
    };

    std::vector<Candidate> candidates;
    candidates.reserve(slots.size());

    for (const auto& [key, slot] : slots) {
        if (slot.type != task_type)  continue;
        if (!is_eligible(slot, now)) continue;

        candidates.push_back(Candidate{key, &slot, score_slot(slot)});
    }

    std::sort(candidates.begin(), candidates.end(),
              [](const Candidate& a, const Candidate& b) {
                  if (a.score != b.score) return a.score < b.score;
                  return is_better_tie(*a.slot, *b.slot);
              });

    std::vector<SlotKey> result;
    result.reserve(static_cast<size_t>(k));
    std::set<uint16_t> used_workers;
    for (const auto& c : candidates) {
        if (used_workers.find(c.key.worker_id) != used_workers.end())
            continue;
        result.push_back(c.key);
        used_workers.insert(c.key.worker_id);
        if (static_cast<int>(result.size()) >= k)
            break;
    }
    return result;
}

} // namespace linep::scheduler
