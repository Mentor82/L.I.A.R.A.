#pragma once

#include <array>
#include <cctype>
#include <chrono>
#include <cstdint>
#include <string>

namespace liara::common {

enum class TaskType : std::uint8_t {
    Instruct = 1,
    Coder = 2,
};

struct TaskRequest {
    std::uint64_t task_id = 0;
    TaskType task_type = TaskType::Instruct;
    std::string input;
    std::uint32_t max_tokens = 256;
    float temperature = 0.2F;
};

struct TaskResult {
    std::uint64_t task_id = 0;
    bool success = false;
    std::string output;
    std::string error_message;
};

struct HelperProfiles {
    bool instruct_ready = false;
    bool coder_ready = false;
};

struct WarmModelResidency {
    bool instruct_warm = false;
    bool coder_warm = false;
};

struct WarmModelMetrics {
    std::chrono::steady_clock::time_point warm_since = std::chrono::steady_clock::now();
    std::uint64_t reload_count = 0;
};

inline std::array<TaskType, 2> required_task_types() {
    return {TaskType::Instruct, TaskType::Coder};
}

inline bool has_required_profiles(const HelperProfiles& profiles) {
    return profiles.instruct_ready && profiles.coder_ready;
}

inline bool has_required_warm_models(const WarmModelResidency& residency) {
    return residency.instruct_warm && residency.coder_warm;
}

inline const char* task_type_name(const TaskType type) {
    switch (type) {
    case TaskType::Instruct:
        return "Instruct";
    case TaskType::Coder:
        return "Coder";
    default:
        return "Unknown";
    }
}

inline TaskType route_helper_task_type(const std::string& helper_task_type) {
    std::string normalized;
    normalized.reserve(helper_task_type.size());
    for (const char ch : helper_task_type) {
        normalized.push_back(static_cast<char>(std::tolower(static_cast<unsigned char>(ch))));
    }

    if (normalized.rfind("code_", 0) == 0 ||
        normalized == "code" ||
        normalized == "coder" ||
        normalized.rfind("coder", 0) == 0) {
        return TaskType::Coder;
    }

    return TaskType::Instruct;
}

inline bool can_serve_task_type(const HelperProfiles& profiles, const TaskType task_type) {
    switch (task_type) {
    case TaskType::Instruct:
        return profiles.instruct_ready;
    case TaskType::Coder:
        return profiles.coder_ready;
    default:
        return false;
    }
}

inline bool can_serve_without_reload(
    const HelperProfiles& profiles,
    const WarmModelResidency& residency,
    const TaskType task_type) {
    if (!can_serve_task_type(profiles, task_type)) {
        return false;
    }

    switch (task_type) {
    case TaskType::Instruct:
        return residency.instruct_warm;
    case TaskType::Coder:
        return residency.coder_warm;
    default:
        return false;
    }
}

inline std::uint64_t warm_age_ms(const WarmModelMetrics& metrics) {
    const auto age = std::chrono::steady_clock::now() - metrics.warm_since;
    return static_cast<std::uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(age).count());
}

inline void mark_reload(WarmModelMetrics& metrics) {
    ++metrics.reload_count;
    metrics.warm_since = std::chrono::steady_clock::now();
}

} // namespace liara::common
