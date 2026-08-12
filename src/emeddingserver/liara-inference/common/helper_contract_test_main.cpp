#include "helper_contract.hpp"
#include "task_contract.hpp"

#include <chrono>
#include <iostream>

namespace {

bool test_success_requires_schema_ok_and_no_missing_fields() {
    liara::common::HelperInferResponse response{};
    response.status = "success";
    response.schema_ok = true;
    response.missing_fields.clear();

    const bool ok = liara::common::is_helper_success(response);
    std::cout << (ok ? "[PASS] " : "[FAIL] ")
              << "success requires schema_ok and empty missing_fields" << '\n';
    return ok;
}

bool test_schema_mismatch_reports_missing_fields() {
    liara::common::HelperInferResponse response{};
    response.status = "success";
    response.schema_ok = false;
    response.missing_fields = {"confidence", "task_id"};

    const bool ok = !liara::common::is_helper_success(response);
    const std::string error = liara::common::helper_error_message(response);
    const bool has_confidence = error.find("confidence") != std::string::npos;
    const bool has_task_id = error.find("task_id") != std::string::npos;

    const bool passed = ok && has_confidence && has_task_id;
    std::cout << (passed ? "[PASS] " : "[FAIL] ")
              << "schema mismatch reports missing fields" << '\n';
    return passed;
}

bool test_required_profiles_instruct_and_coder() {
    liara::common::HelperProfiles ready{};
    ready.instruct_ready = true;
    ready.coder_ready = true;

    const bool ok = liara::common::has_required_profiles(ready);
    std::cout << (ok ? "[PASS] " : "[FAIL] ")
              << "helper requires both Instruct and Coder profiles" << '\n';
    return ok;
}

bool test_missing_coder_profile_fails() {
    liara::common::HelperProfiles missing{};
    missing.instruct_ready = true;
    missing.coder_ready = false;

    const bool ok = !liara::common::has_required_profiles(missing);
    std::cout << (ok ? "[PASS] " : "[FAIL] ")
              << "helper readiness fails when Coder profile is missing" << '\n';
    return ok;
}

bool test_quick_extract_routes_to_instruct() {
    const liara::common::TaskType route = liara::common::route_helper_task_type("quick_extract");
    const bool ok = (route == liara::common::TaskType::Instruct);
    std::cout << (ok ? "[PASS] " : "[FAIL] ")
              << "quick_extract routes to Instruct" << '\n';
    return ok;
}

bool test_code_prefix_routes_to_coder() {
    const liara::common::TaskType route = liara::common::route_helper_task_type("code_fix");
    const bool ok = (route == liara::common::TaskType::Coder);
    std::cout << (ok ? "[PASS] " : "[FAIL] ")
              << "code_* routes to Coder" << '\n';
    return ok;
}

bool test_both_models_must_be_warm() {
    liara::common::WarmModelResidency warm{};
    warm.instruct_warm = true;
    warm.coder_warm = true;

    const bool ok = liara::common::has_required_warm_models(warm);
    std::cout << (ok ? "[PASS] " : "[FAIL] ")
              << "helper keeps Instruct and Coder warm in memory" << '\n';
    return ok;
}

bool test_coder_not_warm_requires_reload() {
    liara::common::HelperProfiles profiles{};
    profiles.instruct_ready = true;
    profiles.coder_ready = true;

    liara::common::WarmModelResidency warm{};
    warm.instruct_warm = true;
    warm.coder_warm = false;

    const bool ok = !liara::common::can_serve_without_reload(
        profiles,
        warm,
        liara::common::TaskType::Coder);
    std::cout << (ok ? "[PASS] " : "[FAIL] ")
              << "coder not warm means no no-reload serving" << '\n';
    return ok;
}

bool test_warm_age_is_reported() {
    liara::common::WarmModelMetrics metrics{};
    metrics.warm_since = std::chrono::steady_clock::now() - std::chrono::milliseconds(20);

    const std::uint64_t age = liara::common::warm_age_ms(metrics);
    const bool ok = age >= 1;
    std::cout << (ok ? "[PASS] " : "[FAIL] ")
              << "warm_age_ms reports non-zero age" << '\n';
    return ok;
}

bool test_mark_reload_increments_counter() {
    liara::common::WarmModelMetrics metrics{};
    metrics.reload_count = 0;
    liara::common::mark_reload(metrics);

    const bool ok = (metrics.reload_count == 1);
    std::cout << (ok ? "[PASS] " : "[FAIL] ")
              << "mark_reload increments reload_count" << '\n';
    return ok;
}

} // namespace

int main() {
    const bool ok1 = test_success_requires_schema_ok_and_no_missing_fields();
    const bool ok2 = test_schema_mismatch_reports_missing_fields();
    const bool ok3 = test_required_profiles_instruct_and_coder();
    const bool ok4 = test_missing_coder_profile_fails();
    const bool ok5 = test_quick_extract_routes_to_instruct();
    const bool ok6 = test_code_prefix_routes_to_coder();
    const bool ok7 = test_both_models_must_be_warm();
    const bool ok8 = test_coder_not_warm_requires_reload();
    const bool ok9 = test_warm_age_is_reported();
    const bool ok10 = test_mark_reload_increments_counter();

    const bool all_ok = ok1 && ok2 && ok3 && ok4 && ok5 && ok6 && ok7 && ok8 && ok9 && ok10;
    std::cout << "\nOverall: " << (all_ok ? "PASS" : "FAIL") << '\n';
    return all_ok ? 0 : 1;
}
