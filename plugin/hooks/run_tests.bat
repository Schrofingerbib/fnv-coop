@echo off
rem One-command L5 tests, using the SHARED build tree (build\all, tools\build\build.py: MSVC env + build lock):
rem   real tests  must report "0 failed"      (and exit 0)
rem   mutant      must report ">0 failed"     (and exit non-zero)
rem Exit code 0 only if both hold. (The single L5 mutant is still a compile-time twin: NVHOOKS_MUTANT touches 8 files.)
setlocal
for %%I in ("%~dp0..\..") do set "REPO=%%~fI"
if not defined PYTHON set "PYTHON=python"
set "BLD=%REPO%\build\all"
"%PYTHON%" -I "%REPO%\tools\build\build.py" --target nvcoop_hooks_tests nvcoop_hooks_tests_mutant --label "L5 hooks" || exit /b 1

"%BLD%\hooks_tests\nvcoop_hooks_tests.exe" > "%BLD%\hooks_real.txt"
set REAL_RC=%ERRORLEVEL%
type "%BLD%\hooks_real.txt"
"%BLD%\hooks_tests\nvcoop_hooks_tests_mutant.exe" > "%BLD%\hooks_mutant.txt"
set MUT_RC=%ERRORLEVEL%
findstr /C:" 0 failed" "%BLD%\hooks_real.txt" >nul
set REAL_OK=%ERRORLEVEL%
findstr /R /C:", [1-9][0-9]* failed" "%BLD%\hooks_mutant.txt" >nul
set MUT_OK=%ERRORLEVEL%
echo real rc=%REAL_RC% zero-failed-line=%REAL_OK%  mutant rc=%MUT_RC% nonzero-failed-line=%MUT_OK%
if not "%REAL_RC%"=="0" exit /b 1
if not "%REAL_OK%"=="0" exit /b 1
if "%MUT_RC%"=="0" exit /b 1
if not "%MUT_OK%"=="0" exit /b 1
echo L5 hooks: real tests pass, mutant rejected - ALL OK
exit /b 0
