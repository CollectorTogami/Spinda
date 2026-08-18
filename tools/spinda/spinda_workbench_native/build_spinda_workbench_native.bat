@echo off
setlocal
set "PATH=C:\msys64\mingw64\bin;%PATH%"
C:\msys64\mingw64\bin\g++.exe -std=c++20 -O3 -march=native -Wall -Wextra -pedantic -static -static-libgcc -static-libstdc++ tools\spinda\spinda_workbench_native\spinda_workbench_native.cpp -o tools\spinda\spinda_workbench_native\spinda_workbench_native.exe -lws2_32
exit /b %ERRORLEVEL%
