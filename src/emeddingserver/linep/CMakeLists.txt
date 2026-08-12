cmake_minimum_required(VERSION 3.20)
project(LiNeP VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Needed for OBJECT libs going into a shared lib on Linux
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

option(LINEP_BUILD_SHARED "Build linep as shared library (DLL / .so)" ON)
option(LINEP_BUILD_TESTS  "Build unit tests"                          ON)

# v1.1 build metadata: compile-time UTC timestamp split into 6 bytes.
string(TIMESTAMP _LINEP_BUILD_YY "%y" UTC)
string(TIMESTAMP _LINEP_BUILD_MM "%m" UTC)
string(TIMESTAMP _LINEP_BUILD_DD "%d" UTC)
string(TIMESTAMP _LINEP_BUILD_HH "%H" UTC)
string(TIMESTAMP _LINEP_BUILD_MI "%M" UTC)
string(TIMESTAMP _LINEP_BUILD_SS "%S" UTC)
math(EXPR _LINEP_BUILD_YY_NUM "${_LINEP_BUILD_YY}")
math(EXPR _LINEP_BUILD_MM_NUM "${_LINEP_BUILD_MM}")
math(EXPR _LINEP_BUILD_DD_NUM "${_LINEP_BUILD_DD}")
math(EXPR _LINEP_BUILD_HH_NUM "${_LINEP_BUILD_HH}")
math(EXPR _LINEP_BUILD_MI_NUM "${_LINEP_BUILD_MI}")
math(EXPR _LINEP_BUILD_SS_NUM "${_LINEP_BUILD_SS}")
add_compile_definitions(
    LINEP_BUILD_YEAR_2D=${_LINEP_BUILD_YY_NUM}
    LINEP_BUILD_MONTH=${_LINEP_BUILD_MM_NUM}
    LINEP_BUILD_DAY=${_LINEP_BUILD_DD_NUM}
    LINEP_BUILD_HOUR=${_LINEP_BUILD_HH_NUM}
    LINEP_BUILD_MINUTE=${_LINEP_BUILD_MI_NUM}
    LINEP_BUILD_SECOND=${_LINEP_BUILD_SS_NUM}
)

# ── Sub-module OBJECT libraries ──────────────────────────────────────────────
add_subdirectory(src/pal)
add_subdirectory(src/core)
add_subdirectory(src/udp)
add_subdirectory(src/tcp)
add_subdirectory(src/scheduler)

# ── Main linep library ───────────────────────────────────────────────────────
if(LINEP_BUILD_SHARED)
    set(_LINEP_TYPE SHARED)
else()
    set(_LINEP_TYPE STATIC)
endif()

add_library(linep ${_LINEP_TYPE}
    $<TARGET_OBJECTS:linep_pal>
    $<TARGET_OBJECTS:linep_core>
    $<TARGET_OBJECTS:linep_udp>
    $<TARGET_OBJECTS:linep_tcp>
    $<TARGET_OBJECTS:linep_scheduler>
)

target_include_directories(linep PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)

if(LINEP_BUILD_SHARED)
    target_compile_definitions(linep
        PRIVATE LINEP_EXPORTS
        PUBLIC  LINEP_SHARED
    )
    # Hide all symbols by default on GCC/Clang; only LINEP_API is visible
    set_target_properties(linep PROPERTIES
        C_VISIBILITY_PRESET     hidden
        CXX_VISIBILITY_PRESET   hidden
        VISIBILITY_INLINES_HIDDEN ON
    )
endif()

if(WIN32)
    target_link_libraries(linep PRIVATE ws2_32)
endif()

set_target_properties(linep PROPERTIES
    VERSION   ${PROJECT_VERSION}
    SOVERSION ${PROJECT_VERSION_MAJOR}
    OUTPUT_NAME linep
)

# ── Tests ────────────────────────────────────────────────────────────────────
if(LINEP_BUILD_TESTS)
    enable_testing()
    add_subdirectory(tests)
endif()
