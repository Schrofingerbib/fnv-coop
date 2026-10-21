@echo off
rem One-command split-screen tests, using the SHARED build tree (build\all, tools\build\build.py: build lock + MSVC env, x86).
rem Runs tools\splitscreen\run_tests.py, which covers: the focus guard (real tests + runtime twins 1..7), the split-screen libraries
rem (config, pad router, import-table patching, focus faking, audio policy; twins 8..34), the glue compile check, the Python tools with
rem their twins, and the engine pins. Prints ALL OK. Never starts the game.
setlocal
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -I "%REPO%\tools\splitscreen\run_tests.py" %* || exit /b 1
exit /b 0
