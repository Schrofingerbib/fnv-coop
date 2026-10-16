@echo off
rem One-command L5 tests: standalone build in %TEMP%\nvh_build, then
rem   real tests  must report "0 failed"      (and exit 0)
rem   mutant      must report ">0 failed"     (and exit non-zero)
rem Exit code 0 only if both hold.
setlocal
set HERE=%~dp0
set BLD=%TEMP%\nvh_build
set CXX=
set CC=
set CMAKE_TOOLCHAIN_FILE=
set CMAKE_GENERATOR=
set CMAKE_ANDROID_NDK=
set PATH=%PATH%;C:\Program Files (x86)\Microsoft Visual Studio\Installer
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cmake -S "%HERE%." -B "%BLD%" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl >nul || exit /b 1
cmake --build "%BLD%" || exit /b 1

"%BLD%\hooks_tests\nvcoop_hooks_tests.exe" > "%BLD%\real.txt"
set REAL_RC=%ERRORLEVEL%
type "%BLD%\real.txt"
"%BLD%\hooks_tests\nvcoop_hooks_tests_mutant.exe" > "%BLD%\mutant.txt"
set MUT_RC=%ERRORLEVEL%
findstr /C:" 0 failed" "%BLD%\real.txt" >nul
set REAL_OK=%ERRORLEVEL%
findstr /R /C:", [1-9][0-9]* failed" "%BLD%\mutant.txt" >nul
set MUT_OK=%ERRORLEVEL%
echo real rc=%REAL_RC% zero-failed-line=%REAL_OK%  mutant rc=%MUT_RC% nonzero-failed-line=%MUT_OK%
if not "%REAL_RC%"=="0" exit /b 1
if not "%REAL_OK%"=="0" exit /b 1
if "%MUT_RC%"=="0" exit /b 1
if not "%MUT_OK%"=="0" exit /b 1
echo L5 hooks: real tests pass, mutant rejected - ALL OK
exit /b 0
