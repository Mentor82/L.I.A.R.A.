# TCP task/result channel — ITcpTaskSender / ITcpTaskReceiver
add_library(linep_tcp OBJECT
    tcp_stub.cpp   # kept for build continuity (empty placeholder)
    tcp.cpp
)
target_sources(linep_tcp
    PUBLIC
        FILE_SET HEADERS
        BASE_DIRS ${CMAKE_CURRENT_SOURCE_DIR}
        FILES tcp.hpp
)

target_link_libraries(linep_tcp PUBLIC linep_core)

target_include_directories(linep_tcp
    PUBLIC  ${CMAKE_CURRENT_SOURCE_DIR}
    PUBLIC  ${PROJECT_SOURCE_DIR}/include
)

if(LINEP_BUILD_SHARED)
    target_compile_definitions(linep_tcp PUBLIC LINEP_SHARED LINEP_EXPORTS)
endif()
