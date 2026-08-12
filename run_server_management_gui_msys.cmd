@echo off
setlocal

set "MSYS_ROOT=%MSYS_ROOT%"
if "%MSYS_ROOT%"=="" set "MSYS_ROOT=C:\msys64"
set "BASH_EXE=%MSYS_ROOT%\usr\bin\bash.exe"

if not exist "%BASH_EXE%" (
  echo [ERROR] MSYS2 bash not found at "%BASH_EXE%"
  echo Set MSYS_ROOT to your MSYS2 installation path and retry.
  exit /b 1
)

set "PROJECT_WIN=%~dp0"
if "%PROJECT_WIN:~-1%"=="\" set "PROJECT_WIN=%PROJECT_WIN:~0,-1%"

"%BASH_EXE%" -lc "set -e; PROJECT_DIR=\"$(cygpath -u '%PROJECT_WIN%')\"; cd \"$PROJECT_DIR\"; if [ -x ../.venv/Scripts/python.exe ]; then ../.venv/Scripts/python.exe server_management_gui.py; elif [ -x .venv/Scripts/python.exe ]; then .venv/Scripts/python.exe server_management_gui.py; else python server_management_gui.py; fi"
exit /b %ERRORLEVEL%
