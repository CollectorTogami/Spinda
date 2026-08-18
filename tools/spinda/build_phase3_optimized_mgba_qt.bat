@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0..\.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

set "BUILD_DIR=%PHASE3_BUILD_DIR%"
if "!BUILD_DIR!"=="" set "BUILD_DIR=%ROOT%\build-mingw64-python-qt"

set "CMAKE_EXE=%CMAKE_EXE%"
if "!CMAKE_EXE!"=="" if exist "C:\msys64\mingw64\bin\cmake.exe" set "CMAKE_EXE=C:\msys64\mingw64\bin\cmake.exe"
if "!CMAKE_EXE!"=="" if exist "C:\devkitPro\msys2\usr\bin\cmake.exe" set "CMAKE_EXE=C:\devkitPro\msys2\usr\bin\cmake.exe"
if "!CMAKE_EXE!"=="" set "CMAKE_EXE=cmake"

if exist "C:\msys64\mingw64\bin\c++.exe" set "PATH=C:\msys64\mingw64\bin;C:\msys64\usr\bin;%PATH%"
if exist "C:\devkitPro\msys2\usr\bin\cmake.exe" set "PATH=C:\devkitPro\msys2\usr\bin;%PATH%"

set "JOBS=%PHASE3_BUILD_JOBS%"
if "!JOBS!"=="" set "JOBS=%NUMBER_OF_PROCESSORS%"
if "!JOBS!"=="" set "JOBS=6"

set "LTO=OFF"
if "%PHASE3_ENABLE_LTO%"=="1" set "LTO=ON"

set "EXTRA_CMAKE_ARGS=%PHASE3_EXTRA_CMAKE_ARGS%"
if "%PHASE3_NATIVE_CPU%"=="1" set "EXTRA_CMAKE_ARGS=!EXTRA_CMAKE_ARGS! -DCMAKE_C_FLAGS_RELEASE=-O3 -DNDEBUG -march=native -DCMAKE_CXX_FLAGS_RELEASE=-O3 -DNDEBUG -march=native"
if "%PHASE3_BUILD_PGO%"=="1" set "EXTRA_CMAKE_ARGS=!EXTRA_CMAKE_ARGS! -DBUILD_PGO=ON"
if "%PHASE3_PGO_STAGE_2%"=="1" set "EXTRA_CMAKE_ARGS=!EXTRA_CMAKE_ARGS! -DPGO_STAGE_2=ON"
if not "%PHASE3_PGO_DIR%"=="" set "EXTRA_CMAKE_ARGS=!EXTRA_CMAKE_ARGS! -DPGO_DIR=%PHASE3_PGO_DIR%"

echo Phase 3 optimized mGBA-Qt build
echo Root: "!ROOT!"
echo Build dir: "!BUILD_DIR!"
echo CMake: "!CMAKE_EXE!"
echo Jobs: !JOBS!
echo LTO: !LTO!
echo Extra CMake args: !EXTRA_CMAKE_ARGS!

"!CMAKE_EXE!" -S "!ROOT!" -B "!BUILD_DIR!" -DCMAKE_BUILD_TYPE=Release -DBUILD_LTO=!LTO! !EXTRA_CMAKE_ARGS!
if errorlevel 1 exit /b %ERRORLEVEL%

"!CMAKE_EXE!" --build "!BUILD_DIR!" --target mgba-qt -j !JOBS!
exit /b %ERRORLEVEL%
