# Cross-compile for Windows ARM64 (MSVC with ARM64 target, or Clang-cl).
# Run from a VS Developer Command Prompt with ARM64 cross tools installed.
set(CMAKE_SYSTEM_NAME    Windows)
set(CMAKE_SYSTEM_PROCESSOR ARM64)

# Clang-cl cross variant (optional override via -DCMAKE_C_COMPILER=clang-cl)
# Default: let CMake pick up the active MSVC ARM64 toolset from the environment.
