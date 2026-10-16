@echo off
rem One-command L12 tests: standalone build in %TEMP%\nvw_build, then
rem   real tests   must report "0 failed"    (exit 0)
rem   every mutant must fail                 (exit non-zero: failed checks or a crash)
rem Exit code 0 only if all of that holds.
setlocal enabledelayedexpansion
set "HERE=%~dp0"
set "BLD=%TEMP%\nvw_build"
set "CXX="
set "CC="
set "CMAKE_TOOLCHAIN_FILE="
set "CMAKE_GENERATOR="
set "PATH=%PATH%;C:\Program Files (x86)\Microsoft Visual Studio\Installer"
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
set "PATH=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
cmake -S "%HERE%." -B "%BLD%" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl >nul || exit /b 1
cmake --build "%BLD%" || exit /b 1

"%BLD%\world_tests\nvcoop_world_tests.exe" > "%BLD%\real.txt"
set REAL_RC=%ERRORLEVEL%
type "%BLD%\real.txt"
findstr /C:", 0 failed" "%BLD%\real.txt" >nul
set REAL_OK=%ERRORLEVEL%

set MUT_BAD=0
set MUT_N=0
for %%F in ("%BLD%\world_tests\nvcoop_world_tests_mut*.exe") do (
  set /a MUT_N+=1
  "%%F" > "%BLD%\%%~nF.txt"
  set RC=!ERRORLEVEL!
  if "!RC!"=="0" ( echo SURVIVED: %%~nF & set /a MUT_BAD+=1 )
)
echo real rc=%REAL_RC% zero-failed-line=%REAL_OK%  mutants=%MUT_N% survivors=%MUT_BAD%
if not "%REAL_RC%"=="0" exit /b 1
if not "%REAL_OK%"=="0" exit /b 1
if not "%MUT_BAD%"=="0" exit /b 1
if "%MUT_N%"=="0" exit /b 1
echo L12 hooks_world: real tests pass, all %MUT_N% mutants killed - ALL OK
exit /b 0
