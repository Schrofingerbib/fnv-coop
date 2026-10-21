@echo off
rem One-command T3b reload tests (x86), using the SHARED build tree (build\all, tools\build\build.py: build lock + MSVC env):
rem   1. registry unit tests  (nvreload_unit):      real must pass; twins FNVCOOP_MUT=1..8 must each fail
rem   2. drain unit tests     (nvreload_drain):     real must pass; twins 7,8,10..14 must each fail
rem        (7 no drain, 8 instruction pointer only, 10 timeout counts as success, 11 ignore list not honoured,
rem         12 only the first thread inspected, 13 no waiting, 14 JSON not escaped)
rem   3. host-process test (nvreload_host_test + nvtestcore.dll): 100 unload/reload cycles with worker threads inside a hooked
rem        detour, a refused reload while a thread is stuck inside a detour, a refused reload for a chained hook;
rem        real must pass; twins 1..9 must each fail (7 and 8 are the drain twins, 9 = hook write guard not wired)
rem   4. the same host test with the loader twins 25 noconfig / 26 configonce (config not copied to the cache): each must fail
rem Twins are switched at RUN time (FNVCOOP_MUT), nothing is recompiled per twin. Prints ALL OK on success.
rem The host test is slow on a loaded machine (minutes per run), hence the long per-run timeout and only 2 parallel jobs.
setlocal
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
set "OUT=%REPO%\out\T3b"
set "SCR=%OUT%\tmp"
if not exist "%SCR%" mkdir "%SCR%"
"%PYTHON%" -I "%REPO%\tools\build\build.py" --target nvreload_unit nvreload_drain nvreload_host_test nvtestcore --label "T3b reload" || (echo BUILD FAILED & exit /b 1)
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "T3b registry" --twins 1-8 --real-regex ", 0 failed" --caught-regex ", [1-9][0-9]* failed" -- "%OUT%\nvreload_unit.exe" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "T3b drain" --twins 7,8,10-14 --real-regex ", 0 failed" --caught-regex ", [1-9][0-9]* failed" -- "%OUT%\nvreload_drain.exe" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "T3b host" --twins 1-9 --real-regex ", 0 failed" --timeout 1200 --jobs 2 -- "%OUT%\nvreload_host_test.exe" "%OUT%\nvtestcore.dll" "%SCR%\host_{n}" || exit /b 1
rem Loader twins 25 (noconfig) and 26 (configonce) of plugin/loader/loader_host.cpp: the same host test, real core, config not copied to the
rem cache. Each must fail BECAUSE the core read no (or a stale) ini, not for another reason.
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "T3b host loader" --twins 25,26 --real-regex ", 0 failed" --caught-regex "read (the ini|nvcoop\.ini)" --timeout 1200 --jobs 2 -- "%OUT%\nvreload_host_test.exe" "%OUT%\nvtestcore.dll" "%SCR%\host_{n}" || exit /b 1
echo ALL OK
exit /b 0
