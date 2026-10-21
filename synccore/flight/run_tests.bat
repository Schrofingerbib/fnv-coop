@echo off
rem T1a flight recorder: build only the flight targets in the SHARED tree build\all (tools\build\build.py: MSVC env, build lock) and run
rem its tests + twins. Prints ALL OK when every test passes.  Usage: synccore\flight\run_tests.bat
setlocal
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
set "OUT=%REPO%\build\all\synccore"
"%PYTHON%" -I "%REPO%\tools\build\build.py" --target flight_tests flight_engine_tests --label "T1a synccore flight" || exit /b 1
ctest --test-dir "%OUT%" -R "flight" -j 2 --output-on-failure || exit /b 1
if not exist "%REPO%\build\all\flight_emit" mkdir "%REPO%\build\all\flight_emit"
set "FNVCOOP_FLIGHT_EMIT=%REPO%\build\all\flight_emit"
"%OUT%\flight\flight_tests.exe" --name emit_fresh_traces_for_the_python_reader >nul || exit /b 1
"%PYTHON%" -I "%~dp0tests\cross_test.py" "%REPO%\build\all\flight_emit" || exit /b 1
echo ALL OK
exit /b 0
