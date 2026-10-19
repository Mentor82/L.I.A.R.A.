cmake_minimum_required(VERSION 3.20)
project(LiaraEmbeddingService LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

find_package(OpenVINO REQUIRED COMPONENTS Runtime)

# Keep optimized production builds diagnosable, including the statically
# embedded LiNeP object libraries. The matching PDB is deployed with the EXE.
if(MSVC)
    add_compile_options(/Zi /Zo)
    add_link_options(/DEBUG:FULL /OPT:REF /OPT:ICF)
endif()

set(LINEP_BUILD_SHARED OFF CACHE BOOL "" FORCE)
set(LINEP_BUILD_TESTS OFF CACHE BOOL "" FORCE)
add_subdirectory(linep)

add_executable(LiaraEmbeddingService
    embedding_server.cpp
    embedding_engine.cpp
    embedding_config.cpp
    embedding_linep.cpp
)

target_include_directories(LiaraEmbeddingService PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}
    ${CMAKE_CURRENT_SOURCE_DIR}/vendor
)

target_link_libraries(LiaraEmbeddingService PRIVATE
    openvino::runtime
    linep
    Ws2_32
)
