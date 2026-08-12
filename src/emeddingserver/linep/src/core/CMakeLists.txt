add_library(linep_core OBJECT
    crc.cpp
    framing.cpp
)

# Inherit PAL includes (socket.hpp, clock.hpp, byteorder.hpp)
target_link_libraries(linep_core PUBLIC linep_pal)

target_include_directories(linep_core
    PUBLIC  ${CMAKE_CURRENT_SOURCE_DIR}
    PUBLIC  ${PROJECT_SOURCE_DIR}/include
)

if(LINEP_BUILD_SHARED)
    target_compile_definitions(linep_core PUBLIC LINEP_SHARED LINEP_EXPORTS)
endif()
