add_library(linep_udp OBJECT
    heartbeat.cpp
)

target_link_libraries(linep_udp PUBLIC linep_core)

target_include_directories(linep_udp
    PUBLIC  ${CMAKE_CURRENT_SOURCE_DIR}
    PUBLIC  ${PROJECT_SOURCE_DIR}/include
)

if(LINEP_BUILD_SHARED)
    target_compile_definitions(linep_udp PUBLIC LINEP_SHARED LINEP_EXPORTS)
endif()
