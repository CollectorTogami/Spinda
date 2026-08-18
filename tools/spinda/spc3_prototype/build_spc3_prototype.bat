@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "MINGW_BIN=C:\msys64\mingw64\bin"
set "CXX=%MINGW_BIN%\g++.exe"
set "OUT=%SCRIPT_DIR%spc3_prototype.exe"
set "ARCH_FLAGS=-march=native"

if /I "%~1"=="baseline" (
  set "OUT=%SCRIPT_DIR%spc3_prototype_baseline.exe"
  set "ARCH_FLAGS=-march=x86-64 -mtune=generic"
)

if not exist "%CXX%" (
  echo Missing compiler: %CXX%
  exit /b 1
)

set "PATH=%MINGW_BIN%;%PATH%"

"%CXX%" -std=c++20 -O3 %ARCH_FLAGS% -Wall -Wextra -pedantic "%SCRIPT_DIR%spc3_prototype.cpp" "%SCRIPT_DIR%spc3_hotloops_x86_64.S" -o "%OUT%" -static -static-libgcc -static-libstdc++ -lz -lzstd -llzma
if errorlevel 1 exit /b %errorlevel%

del /q "%SCRIPT_DIR%libgcc_s_seh-1.dll" "%SCRIPT_DIR%libstdc++-6.dll" "%SCRIPT_DIR%libwinpthread-1.dll" "%SCRIPT_DIR%zlib1.dll" "%SCRIPT_DIR%libzstd.dll" "%SCRIPT_DIR%liblzma-5.dll" 2>nul

echo built %OUT%
