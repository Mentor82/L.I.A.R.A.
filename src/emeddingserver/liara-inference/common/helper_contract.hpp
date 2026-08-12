#pragma once

#include <string>
#include <vector>

namespace liara::common {

struct HelperInferRequest {
    std::string task_id;
    std::string task_type = "quick_extract";
    std::string source_text;
    std::vector<std::string> expected_fields{"task_id", "key_points", "confidence"};
    int max_tokens = 256;
    float temperature = 0.2F;
    std::string model = "openvino-npu";
};

struct HelperInferResponse {
    std::string status = "failed";
    bool schema_ok = false;
    std::vector<std::string> missing_fields;
    std::string parse_error;
    bool normalized = false;
    std::string raw_content;
    std::string parsed_json;
};

inline bool is_helper_success(const HelperInferResponse& response) {
    return response.status == "success" && response.schema_ok && response.missing_fields.empty();
}

inline std::string helper_error_message(const HelperInferResponse& response) {
    if (is_helper_success(response)) {
        return "";
    }
    if (!response.parse_error.empty()) {
        return response.parse_error;
    }
    if (!response.missing_fields.empty()) {
        std::string message = "helper schema mismatch: missing fields";
        for (const auto& field : response.missing_fields) {
            message += " " + field;
        }
        return message;
    }
    return "helper endpoint returned status=" + response.status;
}

} // namespace liara::common