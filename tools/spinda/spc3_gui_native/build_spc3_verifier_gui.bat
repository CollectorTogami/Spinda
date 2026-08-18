@echo off
setlocal
set "PATH=C:\msys64\mingw64\bin;%PATH%"
set "SPC3_GUI_OUTPUT=tools\spinda\spc3_gui_native\spc3_verifier_gui.exe"
set "SPC3_PROTO_OUTPUT=tools\spinda\spc3_prototype\spc3_prototype.exe"
set "SPC3_PROTO_GUI_OUTPUT=tools\spinda\spc3_gui_native\spc3_prototype.exe"
if /I "%~1"=="baseline" (
    set "SPC3_GUI_OUTPUT=tools\spinda\spc3_gui_native\spc3_verifier_gui_baseline.exe"
    set "SPC3_PROTO_OUTPUT=tools\spinda\spc3_prototype\spc3_prototype_baseline.exe"
    set "SPC3_PROTO_GUI_OUTPUT=tools\spinda\spc3_gui_native\spc3_prototype_baseline.exe"
)
call tools\spinda\spc3_prototype\build_spc3_prototype.bat %1
if errorlevel 1 exit /b %ERRORLEVEL%
C:\msys64\mingw64\bin\g++.exe -std=c++20 -O2 -Wall -Wextra -pedantic -static -static-libgcc -static-libstdc++ -mwindows tools\spinda\spc3_gui_native\spc3_verifier_gui.cpp -o "%SPC3_GUI_OUTPUT%" -lcomdlg32 -lshell32 -lole32
if errorlevel 1 exit /b %ERRORLEVEL%
copy /Y "%SPC3_PROTO_OUTPUT%" "%SPC3_PROTO_GUI_OUTPUT%" >nul
if errorlevel 1 exit /b %ERRORLEVEL%
for %%D in (libgcc_s_seh-1.dll libstdc++-6.dll liblzma-5.dll zlib1.dll libzstd.dll) do if exist "tools\spinda\spc3_gui_native\%%D" del /q "tools\spinda\spc3_gui_native\%%D"
exit /b 0
