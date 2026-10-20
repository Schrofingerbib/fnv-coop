@echo off
rem One command: build the co-op fuzzer (Win32/x86 MSVC, Ninja, 2 build jobs) into ..\..\build\T2b-fuzz and run its tests.
rem Prints ALL OK when every test and every mutation twin passes.
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
set "PATH=%VSROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
if "%FUZZ_FLIGHT%"=="" set FUZZ_FLIGHT=ON
set EMFLAG=OFF
if "%FUZZ_EM%"=="1" set EMFLAG=ON
if "%FUZZ_EM%"=="1" set "OUT=%~dp0..\..\build\T2b-fuzz-em"
cmake -S "%~dp0." -B "%OUT%" -DFUZZ_EM=%EMFLAG% -DFUZZ_FLIGHT=%FUZZ_FLIGHT% -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=cl || exit /b 1
cmake --build "%OUT%" || exit /b 1
"%OUT%\fnvcoop_fuzz_tests.exe" %*
exit /b %ERRORLEVEL%
