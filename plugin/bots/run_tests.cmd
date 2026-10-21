@echo off
rem One-command T6a bot-driver tests, using the SHARED build tree (build\all, tools\build\build.py):
rem   real tests must report " 0 failed"; every mutation twin (FNVCOOP_MUT=1..48, switched at RUN time) must report ">0 failed".
rem BOTS_EXE: set NVCOOP_REF_EXE to a reference FalloutNV.exe image to compare the pinned prologues with it.
setlocal
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
if not defined BOTS_EXE if defined NVCOOP_REF_EXE set "BOTS_EXE=%NVCOOP_REF_EXE%"
"%PYTHON%" -I "%REPO%\tools\build\build.py" --target nvbots_tests --label "T6a bots" || exit /b 1
"%PYTHON%" -I "%REPO%\tools\mut\run_twins.py" --label "T6a bots" --twins 1-48 --real-regex " 0 failed" --caught-regex ", [1-9][0-9]* failed" -- "%REPO%\build\all\bots_tests\nvbots_tests.exe" || exit /b 1
exit /b 0
