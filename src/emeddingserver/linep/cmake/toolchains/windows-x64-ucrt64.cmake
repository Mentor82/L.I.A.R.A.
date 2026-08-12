# MSYS2 ucrt64 — GCC for Windows x64, UCRT runtime
# Usage: cmake -B build-ucrt64 -G Ninja
#              -DCMAKE_TOOLCHAIN_FILE=cmake/toolchains/windows-x64-ucrt64.cmake

set(CMAKE_SYSTEM_NAME     Windows)
set(CMAKE_SYSTEM_PROCESSOR x86_64)

set(_UCRT64 "C:/msys64/ucrt64/bin")

set(CMAKE_C_COMPILER   "${_UCRT64}/gcc.exe")
set(CMAKE_CXX_COMPILER "${_UCRT64}/g++.exe")
set(CMAKE_AR           "${_UCRT64}/ar.exe")
set(CMAKE_RANLIB       "${_UCRT64}/gcc-ranlib.exe")
set(CMAKE_NM           "${_UCRT64}/gcc-nm.exe")

# Ninja ships with MSYS2 ucrt64
set(CMAKE_MAKE_PROGRAM "${_UCRT64}/ninja.exe")

# UCRT runtime — not the legacy MSVCRT of Strawberry Perl
