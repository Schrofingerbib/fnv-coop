@echo off
rem One-command tests of the whole avatar chain (plugin/avatar/full), using the SHARED build tree (build\all, tools\build\build.py):
rem   1. avatar_full_tests: three machines on a lossy link with the real AvatarRuntime + AvatarNet + live codec; must report ", 0 failed"
rem      every runtime mutation twin (FNVCOOP_MUT = 101..109, in plugin/coop/live/avatar_wire.cpp and avatar_net.cpp) must report ">0 failed"
rem   2. the suites that share the changed code still pass: avatar_tests (driver, ghost-ref API, hit router, 36 twins), avatar_engine_tests
rem      (real puppet engine, 69 twins), nvcoop_live_tests (dll-wiring's hub / avatar net suite, 34 twins)
rem   3. tools\avatar\proof_avatars.py --selftest: the two-machine in-game proof's analysis against a scripted fake game + injected defects
rem Prints "avatar full: ALL OK" at the end. Nothing here starts a game.
setlocal
for %%I in ("%~dp0..\..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -I "%REPO%\tools\build\build.py" --target avatar_full_tests avatar_tests avatar_engine_tests nvcoop_live_tests --label "avatar full" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "avatar full" --twins 101-109 --real-regex ", 0 failed" --caught-regex ", [1-9][0-9]* failed" -- "%REPO%\build\all\avatar_tests\avatar_full_tests.exe" || exit /b 1
"%REPO%\build\all\avatar_tests\avatar_tests.exe" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "avatar engine (shared)" --twins 1-34,40-56,60-71,80-85 --real-regex ", 0 failed" --caught-regex ", [1-9][0-9]* failed" -- "%REPO%\build\all\avatar_tests\avatar_engine_tests.exe" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "live wiring (shared)" --twins 1-4,11-23,31-33,41-45,47-51,61-64 --real-regex ", 0 failed" --caught-regex ", [1-9][0-9]* failed" -- "%REPO%\build\all\live_tests\nvcoop_live_tests.exe" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\avatar\proof_avatars.py" --selftest || exit /b 1
echo avatar full: ALL OK
exit /b 0
