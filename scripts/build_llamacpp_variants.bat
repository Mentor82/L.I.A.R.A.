@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "VSDEV=C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"
if not exist "%VSDEV%" set "VSDEV=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\Tools\VsDevCmd.bat"
set "ONEAPI=C:\Program Files (x86)\Intel\oneAPI\setvars.bat"
set "CMAKE_EXE=C:\Program Files\CMake\bin\cmake.exe"
set "SRC=C:\ai\LIARA\src\llama.cpp"

if not exist "%VSDEV%" (
  echo [ERROR] VsDevCmd.bat not found.
  exit /b 1
)
if not exist "%ONEAPI%" (
  echo [ERROR] oneAPI setvars.bat not found.
  exit /b 1
)
if not exist "%CMAKE_EXE%" (
  echo [ERROR] cmake.exe not found.
  exit /b 1
)
if not exist "%SRC%" (
  echo [ERROR] Source dir not found: %SRC%
  exit /b 1
)

echo [INFO] Activating Visual Studio toolchain...
call "%VSDEV%" -arch=x64
if errorlevel 1 exit /b 1

echo [INFO] Activating oneAPI environment...
call "%ONEAPI%" intel64 --force >nul
if errorlevel 1 exit /b 1

cd /d "%SRC%"

echo.
echo ===== BUILD 1/3: SYCL + FP16 + ALL TOOLS =====
"%CMAKE_EXE%" --preset x64-windows-sycl-release-f16 -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_EXAMPLES=ON -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=OFF
if errorlevel 1 exit /b 1
"%CMAKE_EXE%" --build build-x64-windows-sycl-release-f16 --config Release -j 8
if errorlevel 1 exit /b 1

echo.
echo ===== BUILD 2/3: VULKAN + ALL TOOLS =====
"%CMAKE_EXE%" --preset x64-windows-vulkan-release -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_EXAMPLES=ON -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=OFF
if errorlevel 1 exit /b 1
"%CMAKE_EXE%" --build build-x64-windows-vulkan-release --config Release -j 8
if errorlevel 1 exit /b 1

echo.
echo ===== BUILD 3/3: CPU + F16C + ALL TOOLS =====
"%CMAKE_EXE%" --preset x64-windows-msvc-release -DGGML_VULKAN=OFF -DGGML_SYCL=OFF -DGGML_F16C=ON -DLLAMA_BUILD_TOOLS=ON -DLLAMA_BUILD_EXAMPLES=ON -DLLAMA_BUILD_SERVER=ON -DLLAMA_BUILD_TESTS=OFF
if errorlevel 1 exit /b 1
"%CMAKE_EXE%" --build build-x64-windows-msvc-release --config RelWithDebInfo -j 8
if errorlevel 1 exit /b 1

echo.
echo [OK] All three builds completed successfully.
exit /b 0
