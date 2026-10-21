@echo off
rem One-command spawn-arb tests, using the SHARED build tree (build\all, tools\build\build.py: build lock + MSVC env):
rem   1. synccore/spawn core: real tests must report ", 0 failed"; every mutation twin (FNVCOOP_MUT=1..28, switched at RUN time) must fail
rem   2. plugin/spawn:        real tests must report ", 0 failed"; twins 41..59 must fail
rem   3. the in-game proof script's own pass / fail logic against a fake world (it is NOT run against a game here)
rem Exit code 0 (and "ALL OK") only if all of that holds.
setlocal
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -I "%REPO%\tools\build\build.py" --target spawn_tests nvcoop_spawn_tests --label "spawn-arb" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "spawn core" --twins 1-28 --real-regex ", 0 failed" --caught-regex ", [1-9][0-9]* failed" -- "%REPO%\build\all\spawn_tests\spawn_tests.exe" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "spawn plugin" --twins 41-59 --real-regex ", 0 failed" --caught-regex ", [1-9][0-9]* failed" -- "%REPO%\build\all\spawn_plugin_tests\nvcoop_spawn_tests.exe" || exit /b 1
"%PYTHON%" -I "%REPO%\plugin\spawn\tools\spawn_proof.py" --selftest || exit /b 1
echo ALL OK
exit /b 0
