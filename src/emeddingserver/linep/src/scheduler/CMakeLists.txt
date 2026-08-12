add_library(linep_scheduler OBJECT
    score_engine.cpp
    scheduler.cpp
)

target_link_libraries(linep_scheduler PUBLIC linep_core linep_pal)

target_include_directories(linep_scheduler
    PUBLIC  ${CMAKE_CURRENT_SOURCE_DIR}
    PUBLIC  ${PROJECT_SOURCE_DIR}/include
)

if(LINEP_BUILD_SHARED)
    target_compile_definitions(linep_scheduler PUBLIC LINEP_SHARED LINEP_EXPORTS)
endif()
