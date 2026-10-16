@echo off
rem L10: build (x86) and run every coop test: in-process unit tests, two-process integration, mutation twins.
setlocal
set "REPO=%~dp0..\.."
call "%~dp0build_coop.cmd" || exit /b 1
echo === coop_tests (config, UDP transport, loopback convergence, lossy host) ===
"%REPO%\out\L10\coop_tests.exe" || exit /b 1
echo === two-process integration + mutation twins ===
python -I "%~dp0tests\test_integration.py" || exit /b 1
echo L10 coop: ALL OK
endlocal
