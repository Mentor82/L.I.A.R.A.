# Each test file is its own executable so they don't collide on main().
# They link object libs directly — no DLL boundary, no dllimport issues.

function(linep_add_test name src)
    add_executable(${name} ${src})
    target_link_libraries(${name} PRIVATE
        $<TARGET_OBJECTS:linep_pal>
        $<TARGET_OBJECTS:linep_core>
        $<TARGET_OBJECTS:linep_udp>
        $<TARGET_OBJECTS:linep_tcp>
        $<TARGET_OBJECTS:linep_scheduler>
    )
    target_include_directories(${name} PRIVATE
        ${PROJECT_SOURCE_DIR}/src/core
        ${PROJECT_SOURCE_DIR}/src/pal
        ${PROJECT_SOURCE_DIR}/src/udp
        ${PROJECT_SOURCE_DIR}/src/scheduler
        ${PROJECT_SOURCE_DIR}/include
    )
    # No LINEP_SHARED / LINEP_EXPORTS — white-box, static link
    if(WIN32)
        target_link_libraries(${name} PRIVATE ws2_32)
    endif()
    add_test(NAME ${name} COMMAND ${name})
endfunction()

linep_add_test(test_crc               test_crc.cpp)
linep_add_test(test_heartbeat_compact test_heartbeat_compact.cpp)
linep_add_test(test_register_frame                 test_register_frame.cpp)
linep_add_test(test_task_send_receive              test_task_send_receive.cpp)
linep_add_test(test_result_parsing                 test_result_parsing.cpp)
linep_add_test(test_error_frame_handling           test_error_frame_handling.cpp)
linep_add_test(test_crc_fail_reject                test_crc_fail_reject.cpp)
linep_add_test(test_timeout_detection              test_timeout_detection.cpp)
linep_add_test(test_slot_state_update_from_heartbeat test_slot_state_update_from_heartbeat.cpp)

# ── Scheduler logic tests (pure in-memory, no network) ────────────────────────
linep_add_test(test_scheduler_selects_lowest_score        test_scheduler_selects_lowest_score.cpp)
linep_add_test(test_scheduler_ignores_not_ready           test_scheduler_ignores_not_ready.cpp)
linep_add_test(test_scheduler_ignores_error_slot          test_scheduler_ignores_error_slot.cpp)
linep_add_test(test_scheduler_ignores_stale_slot          test_scheduler_ignores_stale_slot.cpp)
linep_add_test(test_scheduler_prefers_lower_queue         test_scheduler_prefers_lower_queue.cpp)
linep_add_test(test_scheduler_applies_busy_penalty        test_scheduler_applies_busy_penalty.cpp)
linep_add_test(test_scheduler_applies_degraded_penalty    test_scheduler_applies_degraded_penalty.cpp)
linep_add_test(test_scheduler_cooldown_after_timeout      test_scheduler_cooldown_after_timeout.cpp)
linep_add_test(test_scheduler_retries_on_second_worker    test_scheduler_retries_on_second_worker.cpp)
linep_add_test(test_scheduler_rejects_when_no_slot_available test_scheduler_rejects_when_no_slot_available.cpp)
linep_add_test(test_scheduler_selects_best_slots_with_diversity test_scheduler_selects_best_slots_with_diversity.cpp)
linep_add_test(test_scheduler_selects_best_slots_fallback test_scheduler_selects_best_slots_fallback.cpp)
