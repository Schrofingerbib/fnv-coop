@echo off
rem T1a plugin side: build (x86, shared tree build\all via tools\build\build.py) the Service + runtime tests and run them with their
rem wiring twins (FNVCOOP_MUT=1..3, switched at RUN time, nothing recompiled). Prints ALL OK when everything passes.
rem Also runs the synccore recorder tests (synccore\flight\run_tests.bat).
setlocal
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
call "%REPO%\synccore\flight\run_tests.bat" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\build\build.py" --target flight_runtime_tests --label "T1a plugin flight" || exit /b 1
if not exist "%REPO%\out\T1a" mkdir "%REPO%\out\T1a"
echo === flight_runtime_tests ===
set "FNVCOOP_MUT="
set "FNVCOOP_FLIGHT_KEEP=1"
"%REPO%\out\T1a\flight_runtime_tests.exe" "%REPO%\out\T1a\tmp_real" || exit /b 1
set "FNVCOOP_FLIGHT_KEEP="
"%PYTHON%" -I "%~dp0tests\check_runtime_traces.py" "%REPO%\out\T1a\tmp_real" || exit /b 1
for %%V in (1 2 3) do (
  echo === twin %%V: must FAIL ===
  set "FNVCOOP_MUT=%%V"
  "%REPO%\out\T1a\flight_runtime_tests.exe" "%REPO%\out\T1a\tmp_mut%%V" >nul 2>&1
  if not errorlevel 1 ( echo TWIN %%V WAS NOT CAUGHT & exit /b 1 )
)
set "FNVCOOP_MUT="
echo flight plugin: ALL OK
endlocal
