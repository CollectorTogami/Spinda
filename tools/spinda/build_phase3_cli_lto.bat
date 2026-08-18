@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0..\.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

set "BUILD_DIR=%PHASE3_CLI_BUILD_DIR%"
if "!BUILD_DIR!"=="" set "BUILD_DIR=%ROOT%\build-mingw64-spinda-cli-lto"

set "CMAKE_EXE=%CMAKE_EXE%"
if "!CMAKE_EXE!"=="" if exist "C:\msys64\mingw64\bin\cmake.exe" set "CMAKE_EXE=C:\msys64\mingw64\bin\cmake.exe"
if "!CMAKE_EXE!"=="" if exist "C:\devkitPro\msys2\usr\bin\cmake.exe" set "CMAKE_EXE=C:\devkitPro\msys2\usr\bin\cmake.exe"
if "!CMAKE_EXE!"=="" set "CMAKE_EXE=cmake"

if exist "C:\msys64\mingw64\bin\c++.exe" set "PATH=C:\msys64\mingw64\bin;C:\msys64\usr\bin;%PATH%"
if exist "C:\devkitPro\msys2\usr\bin\cmake.exe" set "PATH=C:\devkitPro\msys2\usr\bin;%PATH%"

set "COMPILER_CMAKE_ARGS="
if exist "C:\msys64\mingw64\bin\x86_64-w64-mingw32-gcc.exe" if exist "C:\msys64\mingw64\bin\x86_64-w64-mingw32-c++.exe" (
    set "COMPILER_CMAKE_ARGS=!COMPILER_CMAKE_ARGS! -DCMAKE_C_COMPILER=C:/msys64/mingw64/bin/x86_64-w64-mingw32-gcc.exe"
    set "COMPILER_CMAKE_ARGS=!COMPILER_CMAKE_ARGS! -DCMAKE_CXX_COMPILER=C:/msys64/mingw64/bin/x86_64-w64-mingw32-c++.exe"
)
if "!COMPILER_CMAKE_ARGS!"=="" if exist "C:\msys64\mingw64\bin\gcc.exe" if exist "C:\msys64\mingw64\bin\c++.exe" (
    set "COMPILER_CMAKE_ARGS=!COMPILER_CMAKE_ARGS! -DCMAKE_C_COMPILER=C:/msys64/mingw64/bin/gcc.exe"
    set "COMPILER_CMAKE_ARGS=!COMPILER_CMAKE_ARGS! -DCMAKE_CXX_COMPILER=C:/msys64/mingw64/bin/c++.exe"
)
if "!COMPILER_CMAKE_ARGS!"=="" if exist "C:\devkitPro\msys2\usr\bin\gcc.exe" if exist "C:\devkitPro\msys2\usr\bin\c++.exe" (
    set "COMPILER_CMAKE_ARGS=!COMPILER_CMAKE_ARGS! -DCMAKE_C_COMPILER=C:/devkitPro/msys2/usr/bin/gcc.exe"
    set "COMPILER_CMAKE_ARGS=!COMPILER_CMAKE_ARGS! -DCMAKE_CXX_COMPILER=C:/devkitPro/msys2/usr/bin/c++.exe"
)

set "JOBS=%PHASE3_CLI_BUILD_JOBS%"
if "!JOBS!"=="" set "JOBS=%NUMBER_OF_PROCESSORS%"
if "!JOBS!"=="" set "JOBS=6"

set "EXTRA_CMAKE_ARGS=%PHASE3_CLI_EXTRA_CMAKE_ARGS%"
set "RELEASE_C_FLAGS=-O3 -DNDEBUG"
set "RELEASE_CXX_FLAGS=-O3 -DNDEBUG"
set "RELEASE_LINKER_FLAGS="
if "%PHASE3_CLI_NATIVE_CPU%"=="1" (
    set "RELEASE_C_FLAGS=!RELEASE_C_FLAGS! -march=native"
    set "RELEASE_CXX_FLAGS=!RELEASE_CXX_FLAGS! -march=native"
)
set "PGO_DIR=%PHASE3_CLI_PGO_DIR%"
if "!PGO_DIR!"=="" set "PGO_DIR=%ROOT%\Phase3SpindaBlocks\_pgo\cli"
set "PGO_DIR_CMAKE=!PGO_DIR:\=/!"
if "%PHASE3_CLI_PGO_GENERATE%"=="1" if "%PHASE3_CLI_PGO_USE%"=="1" (
    echo PHASE3_CLI_PGO_GENERATE and PHASE3_CLI_PGO_USE cannot both be 1.
    exit /b 2
)
if "%PHASE3_CLI_PGO_GENERATE%"=="1" (
    set "RELEASE_C_FLAGS=!RELEASE_C_FLAGS! -fprofile-generate=!PGO_DIR_CMAKE!"
    set "RELEASE_CXX_FLAGS=!RELEASE_CXX_FLAGS! -fprofile-generate=!PGO_DIR_CMAKE!"
    set "RELEASE_LINKER_FLAGS=!RELEASE_LINKER_FLAGS! -fprofile-generate=!PGO_DIR_CMAKE!"
)
if "%PHASE3_CLI_PGO_USE%"=="1" (
    set "RELEASE_C_FLAGS=!RELEASE_C_FLAGS! -fprofile-use=!PGO_DIR_CMAKE! -fprofile-correction -Wno-error=coverage-mismatch"
    set "RELEASE_CXX_FLAGS=!RELEASE_CXX_FLAGS! -fprofile-use=!PGO_DIR_CMAKE! -fprofile-correction -Wno-error=coverage-mismatch"
    set "RELEASE_LINKER_FLAGS=!RELEASE_LINKER_FLAGS! -fprofile-use=!PGO_DIR_CMAKE! -fprofile-correction"
)
set "EXTRA_CMAKE_ARGS=!EXTRA_CMAKE_ARGS! ^"-DCMAKE_C_FLAGS_RELEASE=!RELEASE_C_FLAGS!^" ^"-DCMAKE_CXX_FLAGS_RELEASE=!RELEASE_CXX_FLAGS!^" ^"-DCMAKE_EXE_LINKER_FLAGS_RELEASE=!RELEASE_LINKER_FLAGS!^""

echo Phase 3 headless CLI LTO build
echo Root: "!ROOT!"
echo Build dir: "!BUILD_DIR!"
echo CMake: "!CMAKE_EXE!"
echo Jobs: !JOBS!
echo Extra CMake args: !EXTRA_CMAKE_ARGS!
echo Compiler CMake args: !COMPILER_CMAKE_ARGS!
echo Native CPU: %PHASE3_CLI_NATIVE_CPU%
echo PGO generate: %PHASE3_CLI_PGO_GENERATE%
echo PGO use: %PHASE3_CLI_PGO_USE%
echo PGO dir: !PGO_DIR!
echo PGO CMake dir: !PGO_DIR_CMAKE!

"!CMAKE_EXE!" -S "!ROOT!" -B "!BUILD_DIR!" ^
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 ^
    -DCMAKE_BUILD_TYPE=Release ^
    !COMPILER_CMAKE_ARGS! ^
    -DBUILD_LTO=ON ^
    -DBUILD_SHARED=OFF ^
    -DBUILD_STATIC=ON ^
    -DBUILD_QT=OFF ^
    -DBUILD_SDL=OFF ^
    -DBUILD_PYTHON=OFF ^
    -DBUILD_SPINDA_PHASE3_CLI=ON ^
    -DBUILD_PERF=OFF ^
    -DBUILD_TEST=OFF ^
    -DBUILD_SUITE=OFF ^
    -DBUILD_CINEMA=OFF ^
    -DBUILD_ROM_TEST=OFF ^
    -DBUILD_EXAMPLE=OFF ^
    -DBUILD_LIBRETRO=OFF ^
    -DENABLE_SCRIPTING=OFF ^
    -DUSE_LUA=OFF ^
    -DUSE_DEBUGGERS=OFF ^
    -DUSE_GDB_STUB=OFF ^
    -DUSE_DISCORD_RPC=OFF ^
    -DUSE_FFMPEG=OFF ^
    -DUSE_PNG=OFF ^
    -DUSE_LIBZIP=OFF ^
    -DUSE_MINIZIP=OFF ^
    -DUSE_LZMA=OFF ^
    -DUSE_SQLITE3=OFF ^
    -DUSE_ELF=OFF ^
    -DBUILD_GL=OFF ^
    -DBUILD_GLES2=OFF ^
    -DBUILD_GLES3=OFF ^
    -DM_CORE_GBA=ON ^
    -DM_CORE_GB=OFF ^
    !EXTRA_CMAKE_ARGS!
if errorlevel 1 exit /b %ERRORLEVEL%

"!CMAKE_EXE!" --build "!BUILD_DIR!" --target mgba-spinda-phase3 -j !JOBS!
exit /b %ERRORLEVEL%
