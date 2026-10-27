@echo off
rem One-command synccore/loot tests, using the SHARED build tree (build\all, tools\build\build.py: build lock + MSVC env):
rem   the real suite must report ", 0 failed"; every mutation twin (FNVCOOP_MUT=101..137 and 170..173, switched at RUN time) must fail it.
rem Exit code 0 (and "ALL OK") only if all of that holds.
setlocal
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -I "%REPO%\tools\build\build.py" --target loot_tests --label "loot-core" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "loot core" --twins 101-137,170-173 --real-regex ", 0 failed" --caught-regex ", [1-9][0-9]* failed" -- "%REPO%\build\all\loot_tests\loot_tests.exe" || exit /b 1
echo ALL OK
exit /b 0
