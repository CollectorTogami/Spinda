@echo off
setlocal EnableExtensions EnableDelayedExpansion

set "ROOT=%~dp0..\.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"

set "PYTHON_EXE=%ROOT%\.venv-mgba\bin\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

if "%~1"=="" (
    set /p "WORKERS=Worker count [6]: "
    if "!WORKERS!"=="" set "WORKERS=6"
) else (
    set "WORKERS=%~1"
    shift /1
)

echo(!WORKERS!| "%SystemRoot%\System32\findstr.exe" /R "^[1-9][0-9]*$" >nul
if errorlevel 1 (
    echo Worker count must be a positive integer.
    exit /b 2
)

set "LANES=%PHASE3_LANES%"
if "!LANES!"=="" set "LANES=0x0000-0xFFFF"

set "BUNDLE_SIZE=%PHASE3_BUNDLE_SIZE%"
if "!BUNDLE_SIZE!"=="" set "BUNDLE_SIZE=2"
echo(!BUNDLE_SIZE!| "%SystemRoot%\System32\findstr.exe" /R "^[1-9][0-9]*$" >nul
if errorlevel 1 (
    echo PHASE3_BUNDLE_SIZE must be a positive integer.
    exit /b 2
)

set "ZIP_METHOD=%PHASE3_ZIP_METHOD%"
if "!ZIP_METHOD!"=="" set "ZIP_METHOD=deflate"
if /I not "!ZIP_METHOD!"=="deflate" if /I not "!ZIP_METHOD!"=="store" (
    echo PHASE3_ZIP_METHOD must be deflate or store.
    exit /b 2
)

set "STATUS_WRITE_SECONDS=%PHASE3_STATUS_WRITE_SECONDS%"
if "!STATUS_WRITE_SECONDS!"=="" set "STATUS_WRITE_SECONDS=10"

set "EXTRA_ARGS="
:collect_args
if "%~1"=="" goto args_done
set "EXTRA_ARGS=!EXTRA_ARGS! %1"
shift /1
goto collect_args

:args_done
echo Phase 3 remaining-lane launcher
echo Root: "!ROOT!"
echo Lanes: !LANES!
echo Workers: !WORKERS!
echo Runner: standalone CLI LTO
echo Lane bundle size: !BUNDLE_SIZE!
echo ZIP method: !ZIP_METHOD!
echo Pool status write seconds: !STATUS_WRITE_SECONDS!
echo Existing complete ZIP names: skipped without opening ZIP entries
echo Worker queue: refill until all requested missing lanes finish

"%PYTHON_EXE%" "%ROOT%\tools\spinda\native_phase3_worker_pool.py" ^
    --runner cli ^
    --lanes !LANES! ^
    --workers !WORKERS! ^
    --bundle-size !BUNDLE_SIZE! ^
    --zip-method !ZIP_METHOD! ^
    --cache-dir "%ROOT%\Phase3SpindaBlocks\_cache" ^
    --headless ^
    --overwrite ^
    --skip-existing-by-name ^
    --min-pickup-detect-frame 4 ^
    --fast-pickup-check-first-frame 4 ^
    --fast-pickup-check-second-frame 5 ^
    --poll-seconds 2 ^
    --status-write-seconds !STATUS_WRITE_SECONDS! ^
    !EXTRA_ARGS!

exit /b %ERRORLEVEL%
