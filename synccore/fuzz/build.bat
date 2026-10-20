@echo off
rem Build only (no tests): fnvcoop_simfuzz.exe and fnvcoop_fuzz_tests.exe into build\T2b-fuzz (or build\T2b-fuzz-em with FUZZ_EM=1).
setlocal
set "VSROOT=C:\Program Files\Microsoft Visual Studio\2022\Community"
set "PATH=C:\Program Files (x86)\Microsoft Visual Studio\Installer;%PATH%"
set CC=
set CXX=
set CMAKE_TOOLCHAIN_FILE=
set ANDROID_NDK=
set CMAKE_BUILD_PARALLEL_LEVEL=2
call "%VSROOT%\VC\Auxiliary\Build\vcvars32.bat" >nul
set "OUT=%~dp0..\..\build\T2b-fuzz"
if "%FUZZ_FLIGHT%"=="" set FUZZ_FLIGHT=ON
set EMFLAG=OFF
if "%FUZZ_EM%"=="1" set EMFLAG=ON
if "%FUZZ_EM%"=="1" set "OUT=%~dp0..\..\build\T2b-fuzz-em"
set "PATH=%VSROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
cmake -S "%~dp0." -B "%OUT%" -DFUZZ_EM=%EMFLAG% -DFUZZ_FLIGHT=%FUZZ_FLIGHT% -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl >nul || exit /b 1
cmake --build "%OUT%" || exit /b 1
exit /b 0
