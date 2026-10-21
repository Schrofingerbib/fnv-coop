@echo off
rem One-command world-time tests, using the SHARED build tree (build\all, tools\build\build.py: build lock + MSVC env):
rem   1. plugin/world_time core: real tests must report ", 0 failed"; every mutation twin (FNVCOOP_MUT=101..166, switched at RUN time) must fail
rem   2. the in-game proof script's own pass / fail logic against a fake bridge (it is NOT run against a game here)
rem   3. docs/tools/world-time.md and docs/protocol.md must agree with the code (channel byte, version)
rem Exit code 0 (and "ALL OK") only if all of that holds.
setlocal
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -I "%REPO%\tools\build\build.py" --target nvcoop_wt_tests --label "world-time" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "world-time" --twins 101-124,131-139,146-169 --real-regex ", 0 failed" --caught-regex ", [1-9][0-9]* failed" -- "%REPO%\build\all\wt_tests\nvcoop_wt_tests.exe" || exit /b 1
"%PYTHON%" -I "%REPO%\plugin\world_time\tools\worldtime_proof.py" --selftest || exit /b 1
echo ALL OK
exit /b 0
