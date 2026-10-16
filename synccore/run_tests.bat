@echo off
rem One command: configure (Win32/x86 MSVC, Ninja), build Release into ..\build\L4, run every test.
rem Usage: synccore\run_tests.bat [x86|x64]   (x86 default: matches the game plugin)
setlocal
set ARCH=%1
if "%ARCH%"=="" set ARCH=x86
set "VSROOT=C:\Program Files\Microsoft Visual Studio\2022\Community"
set "PATH=C:\Program Files (x86)\Microsoft Visual Studio\Installer;%PATH%"
set CC=
set CXX=
set CMAKE_TOOLCHAIN_FILE=
set ANDROID_NDK=
if "%ARCH%"=="x64" goto vc64
call "%VSROOT%\VC\Auxiliary\Build\vcvars32.bat" >nul
set "OUT=%~dp0..\build\L4"
goto vcdone
:vc64
call "%VSROOT%\VC\Auxiliary\Build\vcvars64.bat" >nul
set "OUT=%~dp0..\build\L4-x64"
:vcdone
set "PATH=%VSROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
cmake -S "%~dp0." -B "%OUT%" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=cl || exit /b 1
cmake --build "%OUT%" || exit /b 1
ctest --test-dir "%OUT%" --output-on-failure
exit /b %ERRORLEVEL%
