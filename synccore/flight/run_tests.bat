@echo off
rem T1a flight recorder: build only the flight targets (Win32/x86, Release, 2 jobs) into build\T1a and run its tests + twins.
rem Prints ALL OK when every test passes.  Usage: synccore\flight\run_tests.bat
setlocal
set "VSROOT=C:\Program Files\Microsoft Visual Studio\2022\Community"
set "PATH=C:\Program Files (x86)\Microsoft Visual Studio\Installer;%PATH%"
set CC=
set CXX=
set CMAKE_TOOLCHAIN_FILE=
set ANDROID_NDK=
set CMAKE_BUILD_PARALLEL_LEVEL=2
call "%VSROOT%\VC\Auxiliary\Build\vcvars32.bat" >nul
set "PATH=%VSROOT%\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
set "SRC=%~dp0.."
set "OUT=%~dp0..\..\build\T1a"
cmake -S "%SRC%" -B "%OUT%" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_COMPILER=cl -DCMAKE_CXX_COMPILER=cl || exit /b 1
cmake --build "%OUT%" --target flight_tests flight_engine_tests || exit /b 1
ctest --test-dir "%OUT%" -R "flight" --output-on-failure || exit /b 1
if not exist "%OUT%\emit" mkdir "%OUT%\emit"
set "FNVCOOP_FLIGHT_EMIT=%OUT%\emit"
"%OUT%\flight\flight_tests.exe" --name emit_fresh_traces_for_the_python_reader >nul || exit /b 1
python -I "%~dp0tests\cross_test.py" "%OUT%\emit" || exit /b 1
echo ALL OK
exit /b 0
