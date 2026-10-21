@echo off
rem plugin/actors/engine: one-command tests, using the SHARED build tree (build\all, tools\build\build.py: build lock + MSVC env):
rem   the real run must report ", 0 failed" (exit 0); every mutation twin (FNVCOOP_MUT=n, switched at RUN time, nothing recompiled) must fail.
rem Twin numbers: 1-6 script_engine, 7-10 and 65 ledger, 11-39 engine_port, 41-60 and 66 actor_feed, 61-63 actor_engine_api, 64 engine_port (sampling) (40 is unused).
rem Exit code 0 only if all of that holds; prints ALL OK.
setlocal
for %%I in ("%~dp0..\..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -I "%REPO%\tools\build\build.py" --target nvcoop_actor_engine_tests --label "M3 actor port" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "M3 actor port" --twins 1-39,41-57,59-66 --real-regex ", 0 failed" --caught-regex ", [1-9][0-9]* failed" -- "%REPO%\build\all\actor_engine_tests\nvcoop_actor_engine_tests.exe" || exit /b 1
exit /b 0
