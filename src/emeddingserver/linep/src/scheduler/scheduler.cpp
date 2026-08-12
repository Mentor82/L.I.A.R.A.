#include "scheduler.hpp"
#include "score_engine.hpp"
#include "../core/framing.hpp"
#include "../pal/socket.hpp"

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <condition_variable>
#include <cstring>
#include <deque>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

namespace linep::scheduler {

class SchedulerImpl final : public IScheduler {
public:
    SchedulerImpl()  = default;
    ~SchedulerImpl() override { stop(); }

    // ── IScheduler ────────────────────────────────────────────────────────────

    void register_slot(uint16_t wid, uint8_t sid,
                       linep::TaskType t,
                       const char* ip, uint16_t tcp_port) override
    {
        const SlotKey key{wid, sid};
        std::lock_guard<std::mutex> lk(slots_mu_);
        auto& slot    = slots_[key];
        slot.worker_id = wid;
        slot.slot_id   = sid;
        slot.type      = t;
        slot.tcp_port  = tcp_port;
        std::strncpy(slot.ip, ip, sizeof(slot.ip) - 1u);
    }

    void apply_heartbeat(const linep::HeartbeatCompact& hb) override
    {
        const SlotKey key{hb.worker_id, hb.slot_id};
        std::lock_guard<std::mutex> lk(slots_mu_);
        auto& slot     = slots_[key];
        slot.worker_id = hb.worker_id;
        slot.slot_id   = hb.slot_id;
        linep::scheduler::apply_heartbeat(slot, hb);
    }

    uint32_t submit(linep::TaskType  type,
                    const uint8_t*   payload, uint32_t len,
                    uint32_t         timeout_ms, uint32_t max_attempts,
                    ResultCallback   callback,   void* user_data) override
    {
        const uint32_t corr_id = corr_gen_.fetch_add(1u);
        PendingTask t;
        t.correlation_id = corr_id;
        t.type           = type;
        t.payload.assign(payload, payload + len);
        t.timeout_ms     = timeout_ms;
        t.max_attempts   = max_attempts;
        t.callback       = callback;
        t.user_data      = user_data;
        {
            std::lock_guard<std::mutex> lk(pending_mu_);
            pending_.push_back(std::move(t));
        }
        pending_cv_.notify_one();
        return corr_id;
    }

    bool start() override
    {
        if (running_.exchange(true)) return false;
        pal::net_init();
        loop_thread_ = std::thread(&SchedulerImpl::run_loop, this);
        return true;
    }

    void stop() override
    {
        if (!running_.exchange(false)) return;
        pending_cv_.notify_all();
        if (loop_thread_.joinable()) loop_thread_.join();
        // Wait for all in-flight dispatch threads.
        while (active_dispatch_count_.load() > 0)
            std::this_thread::sleep_for(std::chrono::milliseconds(5));
        pal::net_cleanup();
    }

private:

    struct PartialResult {
        SlotKey             slot{};
        linep::ResultStatus status{linep::RESULT_TIMEOUT};
        std::vector<uint8_t> payload;
    };

    struct SharedDispatchState {
        PendingTask                 task;
        std::atomic<bool>           done{false};
        std::mutex                  result_lock;
        std::vector<PartialResult>  partial_results;
        std::atomic<int>            remaining{0};
    };

    // ── Main loop ─────────────────────────────────────────────────────────────

    void run_loop()
    {
        while (running_.load()) {
            const auto now = std::chrono::steady_clock::now();
            expire_stale(now);
            dispatch_pending(now);
            std::unique_lock<std::mutex> lk(pending_mu_);
            pending_cv_.wait_for(lk, std::chrono::milliseconds(10), [this] {
                return !pending_.empty() || !running_.load();
            });
        }
    }

    void expire_stale(std::chrono::steady_clock::time_point now)
    {
        std::lock_guard<std::mutex> lk(slots_mu_);
        for (auto& [key, slot] : slots_) {
            if (slot.last_heartbeat != std::chrono::steady_clock::time_point{} &&
                now > slot.last_heartbeat + HEARTBEAT_TIMEOUT)
            {
                expire_slot(slot);
            }
        }
    }

    // ── Dispatch ──────────────────────────────────────────────────────────────

    void dispatch_pending(std::chrono::steady_clock::time_point now)
    {
        // Snapshot — avoid holding pending_mu_ while acquiring slots_mu_.
        std::vector<PendingTask> snapshot;
        {
            std::lock_guard<std::mutex> lk(pending_mu_);
            if (pending_.empty()) return;
            snapshot.assign(pending_.begin(), pending_.end());
        }

        std::vector<uint32_t> dispatched;
        for (auto& task : snapshot) {
            std::vector<SlotKey> selected;
            std::vector<double> selected_scores;
            {
                std::lock_guard<std::mutex> sl(slots_mu_);
                selected = select_best_slots(slots_, task.type, now, 3);
                selected_scores.reserve(selected.size());
                for (const auto& key : selected) {
                    auto it = slots_.find(key);
                    if (it == slots_.end()) continue;
                    selected_scores.push_back(score_slot(it->second));
                    it->second.busy = true;
                    it->second.last_used = now;
                }
            }

            dispatched.push_back(task.correlation_id);

            if (selected.empty()) {
                std::fprintf(stderr,
                             "[scheduler] corr=%u eligible_slots=0 fallback=0 reject=ERR_NO_SLOT_AVAILABLE\n",
                             task.correlation_id);
                if (task.callback) {
                    task.callback(task.correlation_id,
                                  linep::RESULT_REJECTED,
                                  nullptr,
                                  0u,
                                  task.user_data);
                }
                continue;
            }

            std::fprintf(stderr,
                         "[scheduler] corr=%u dispatch_slots=%zu fallback=%zu scores=",
                         task.correlation_id,
                         selected.size(),
                         selected.size());
            for (size_t i = 0; i < selected_scores.size(); ++i) {
                std::fprintf(stderr,
                             "%s%.2f",
                             (i == 0u ? "" : ","),
                             selected_scores[i]);
            }
            std::fprintf(stderr, "\n");

            auto shared = std::make_shared<SharedDispatchState>();
            shared->task = task;
            shared->task.attempt_count++;
            shared->remaining.store(static_cast<int>(selected.size()));

            for (const auto& slot : selected) {
                ActiveTask at;
                at.task          = shared->task;
                at.assigned_slot = slot;
                at.started_at    = now;

                active_dispatch_count_.fetch_add(1);
                std::thread(&SchedulerImpl::dispatch_one_slot,
                            this,
                            std::move(at),
                            shared).detach();
            }
        }

        if (!dispatched.empty()) {
            std::lock_guard<std::mutex> lk(pending_mu_);
            pending_.erase(
                std::remove_if(pending_.begin(), pending_.end(),
                    [&](const PendingTask& t) {
                        for (auto id : dispatched)
                            if (t.correlation_id == id) return true;
                        return false;
                    }),
                pending_.end());
        }
    }

    bool complete_task_if_first(const std::shared_ptr<SharedDispatchState>& shared,
                                linep::ResultStatus status,
                                const std::vector<uint8_t>& payload)
    {
        bool expected = false;
        if (!shared->done.compare_exchange_strong(expected, true))
            return false;

        if (shared->task.callback) {
            shared->task.callback(shared->task.correlation_id,
                                  status,
                                  payload.empty() ? nullptr : payload.data(),
                                  static_cast<uint32_t>(payload.size()),
                                  shared->task.user_data);
        }
        return true;
    }

    void on_slot_transport_failure(const SlotKey& key)
    {
        std::lock_guard<std::mutex> lk(slots_mu_);
        auto it = slots_.find(key);
        if (it != slots_.end()) {
            it->second.busy = false;
            it->second.timeout_count++;
            it->second.cooldown_until =
                std::chrono::steady_clock::now() +
                cooldown_for(it->second.timeout_count);
        }
    }

    bool dispatch_transport(ActiveTask at, PartialResult& out)
    {
        // Capture endpoint before touching the slot registry.
        std::string  ip;
        uint16_t     tcp_port = 0u;
        {
            std::lock_guard<std::mutex> lk(slots_mu_);
            auto it = slots_.find(at.assigned_slot);
            if (it == slots_.end()) {
                return false;
            }
            ip       = it->second.ip;
            tcp_port = it->second.tcp_port;
        }

        pal::Socket c = pal::tcp_connect(ip.c_str(), tcp_port, at.task.timeout_ms);
        if (!c.valid()) {
            on_slot_transport_failure(at.assigned_slot);
            return false;
        }

        // Send TASK header + payload.
        const auto h = core::make_header(
            static_cast<uint8_t>(linep::MsgType::TASK),
            0u,
            static_cast<uint32_t>(at.task.payload.size()),
            at.task.correlation_id,
            at.task.correlation_id,
            at.assigned_slot.worker_id,
            at.assigned_slot.slot_id);

        if (pal::tcp_send_all(c,
                reinterpret_cast<const uint8_t*>(&h),
                static_cast<int>(sizeof(h))) != static_cast<int>(sizeof(h))) {
            pal::socket_close(c);
            on_slot_transport_failure(at.assigned_slot);
            return false;
        }
        if (!at.task.payload.empty()) {
            if (pal::tcp_send_all(c,
                    at.task.payload.data(),
                    static_cast<int>(at.task.payload.size()))
                        != static_cast<int>(at.task.payload.size())) {
                pal::socket_close(c);
                on_slot_transport_failure(at.assigned_slot);
                return false;
            }
        }

        // Receive response header.
        linep::Header res_h{};
        int r = pal::tcp_recv_all(c,
                    reinterpret_cast<uint8_t*>(&res_h),
                    static_cast<int>(sizeof(res_h)));
        if (r != static_cast<int>(sizeof(res_h)) || !core::validate_header(res_h)) {
            pal::socket_close(c);
            on_slot_transport_failure(at.assigned_slot);
            return false;
        }

        std::vector<uint8_t> res_payload(res_h.payload_len);
        if (!res_payload.empty()) {
            r = pal::tcp_recv_all(c,
                        res_payload.data(),
                        static_cast<int>(res_payload.size()));
            if (r != static_cast<int>(res_payload.size())) {
                pal::socket_close(c);
                on_slot_transport_failure(at.assigned_slot);
                return false;
            }
        }
        pal::socket_close(c);

        // Parse ResultStatus from first payload byte (as per framing convention).
        linep::ResultStatus status = linep::RESULT_OK;
        if (!res_payload.empty()) {
            status = static_cast<linep::ResultStatus>(res_payload[0]);
            res_payload.erase(res_payload.begin());
        }
        if (res_h.msg_type == static_cast<uint8_t>(linep::MsgType::MSG_ERROR))
            status = linep::RESULT_MODEL_ERROR;

        // Mark slot no longer busy, increment success counter.
        {
            std::lock_guard<std::mutex> lk(slots_mu_);
            auto it = slots_.find(at.assigned_slot);
            if (it != slots_.end()) {
                it->second.busy = false;
                it->second.success_count++;
            }
        }

        out.slot = at.assigned_slot;
        out.status = status;
        out.payload = std::move(res_payload);
        return true;
    }

    void dispatch_one_slot(ActiveTask at, std::shared_ptr<SharedDispatchState> shared)
    {
        PartialResult partial{};
        if (dispatch_transport(at, partial)) {
            {
                std::lock_guard<std::mutex> lk(shared->result_lock);
                shared->partial_results.push_back(partial);
            }

            if (partial.status == linep::RESULT_OK) {
                complete_task_if_first(shared, partial.status, partial.payload);
            }
        }

        if (shared->remaining.fetch_sub(1) == 1) {
            if (!shared->done.load()) {
                std::vector<PartialResult> results;
                {
                    std::lock_guard<std::mutex> lk(shared->result_lock);
                    results = shared->partial_results;
                }

                if (!results.empty()) {
                    int ok_count = 0;
                    for (const auto& r : results) {
                        if (r.status == linep::RESULT_OK) ok_count++;
                    }
                    const int consensus_level = (ok_count >= 3) ? 2 : ((ok_count >= 2) ? 1 : 0);
                    std::fprintf(stderr,
                                 "[scheduler] corr=%u consensus_level=%d partial_results=%zu\n",
                                 shared->task.correlation_id,
                                 consensus_level,
                                 results.size());

                    const auto* chosen = &results.front();
                    for (const auto& r : results) {
                        if (r.status == linep::RESULT_OK) {
                            chosen = &r;
                            break;
                        }
                    }
                    complete_task_if_first(shared, chosen->status, chosen->payload);
                } else if (shared->task.attempt_count < shared->task.max_attempts) {
                    std::lock_guard<std::mutex> lk(pending_mu_);
                    pending_.push_back(shared->task);
                    pending_cv_.notify_one();
                } else {
                    complete_task_if_first(shared, linep::RESULT_TIMEOUT, {});
                }
            }
        }

        active_dispatch_count_.fetch_sub(1);
    }

    // ── State ─────────────────────────────────────────────────────────────────

    std::map<SlotKey, SlotState> slots_;
    mutable std::mutex           slots_mu_;

    std::deque<PendingTask> pending_;
    std::mutex              pending_mu_;
    std::condition_variable pending_cv_;

    std::atomic<bool>     running_{false};
    std::atomic<int>      active_dispatch_count_{0};
    std::thread           loop_thread_;
    std::atomic<uint32_t> corr_gen_{1u};
};

// ── Factory ───────────────────────────────────────────────────────────────────

IScheduler* create_scheduler()            { return new SchedulerImpl(); }
void         destroy_scheduler(IScheduler* p) { delete p; }

} // namespace linep::scheduler
