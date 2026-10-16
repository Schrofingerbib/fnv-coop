@echo off
rem Build nvcoop.dll (Win32/x86) + fake-host test exes into out\L1 using VS 2022 Build Tools + its bundled Ninja.
rem Usage: tools\runner\build_l1.cmd [clean]
setlocal
set "CMAKE_TOOLCHAIN_FILE="
set "ANDROID_NDK_HOME="
set "CXX="
set "CC="
set "REPO=%~dp0..\.."
set "PATH=C:\Program Files (x86)\Microsoft Visual Studio\Installer;%PATH%"
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat" >nul || exit /b 1
set "PATH=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
if /i "%1"=="clean" rmdir /s /q "%REPO%\build\L1" 2>nul
cmake -S "%REPO%\plugin" -B "%REPO%\build\L1" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl %CMAKE_EXTRA% || exit /b 1
cmake --build "%REPO%\build\L1" || exit /b 1
rem keep disk small: drop intermediates, keep only the cache + out\L1
if exist "%REPO%\build\L1\CMakeFiles" del /s /q "%REPO%\build\L1\*.obj" >nul 2>&1
endlocal
