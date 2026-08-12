if(WIN32)
    set(_PAL_SRCS socket_win.cpp clock_win.cpp)
else()
    set(_PAL_SRCS socket_posix.cpp clock_posix.cpp)
endif()

add_library(linep_pal OBJECT ${_PAL_SRCS})

target_include_directories(linep_pal
    PUBLIC  ${CMAKE_CURRENT_SOURCE_DIR}        # internal headers
    PUBLIC  ${PROJECT_SOURCE_DIR}/include      # linep/export.h etc.
)

if(LINEP_BUILD_SHARED)
    target_compile_definitions(linep_pal PUBLIC LINEP_SHARED LINEP_EXPORTS)
endif()
