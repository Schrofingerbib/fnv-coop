@echo off
rem L10: build (x86) and run every coop test: in-process unit tests, two-process integration, mutation twins.
setlocal enabledelayedexpansion
set "REPO=%~dp0..\.."
call "%~dp0build_coop.cmd" || exit /b 1
echo === coop_tests (config, UDP transport, loopback convergence, lossy host) ===
"%REPO%\out\L10\coop_tests.exe" || exit /b 1
echo === L11 wiring twins (each dropped wiring call must make the --ui tests fail) ===
for %%N in (1 2 3 4 5) do (
  set "COOP_UI_MUTANT=%%N"
  "%REPO%\out\L10\coop_tests_ui_mut.exe" --ui >nul 2>&1
  if !errorlevel! EQU 0 ( echo TWIN SURVIVED: COOP_UI_MUTANT=%%N & exit /b 1 )
  echo   twin %%N killed
)
set "COOP_UI_MUTANT="
echo === leave / join twins ===
for %%N in (1 2) do (
  set "COOP_LEAVE_MUTANT=%%N"
  "%REPO%\out\L10\coop_tests_ui_mut.exe" --leave >nul 2>&1
  if !errorlevel! EQU 0 ( echo TWIN SURVIVED: COOP_LEAVE_MUTANT=%%N & exit /b 1 )
  echo   twin %%N killed
)
set "COOP_LEAVE_MUTANT="
echo === two-process integration + mutation twins ===
python -I "%~dp0tests\test_integration.py" || exit /b 1
echo L10 coop: ALL OK
endlocal
