@echo off
rem Build nvcoop.dll (Win32/x86) + fake-host test exes into out\L1 using VS 2022 Build Tools + its bundled Ninja.
rem Since the build-speed work this uses the ONE shared build tree build\all (root CMakeLists.txt, tools\build\build.py: MSVC env,
rem build lock out\locks\build.lock, sccache if installed) and builds the plugin part of it ("plugin/all").
rem Usage: tools\runner\build_l1.cmd [clean]      CMAKE_EXTRA="-DNVCOOP_SPLIT_LOADER=ON ..." adds cmake arguments (space separated)
setlocal EnableDelayedExpansion
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
set "XARGS="
for %%A in (%CMAKE_EXTRA%) do set "XARGS=!XARGS! --cmake-arg=%%A"
set "CLEAN="
if /i "%1"=="clean" set "CLEAN=--clean"
"%PYTHON%" -I "%REPO%\tools\build\build.py" --target plugin/all --label L1 %CLEAN% %XARGS% || exit /b 1
endlocal
