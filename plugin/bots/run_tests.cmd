@echo off
rem One-command T6a bot-driver tests: standalone build in %TEMP%\nvbots_build, then
rem   real tests must report "0 failed"; every mutant (NVBOTS_MUTANT=1..48) must report ">0 failed".
rem BOTS_EXE: set NVCOOP_REF_EXE to a reference FalloutNV.exe image to compare the pinned prologues with it.
setlocal EnableDelayedExpansion
set HERE=%~dp0
set BLD=%TEMP%\nvbots_build
set CXX=
set CC=
set CMAKE_TOOLCHAIN_FILE=
set CMAKE_GENERATOR=
set CMAKE_ANDROID_NDK=
if not defined CMAKE_BUILD_PARALLEL_LEVEL set CMAKE_BUILD_PARALLEL_LEVEL=2
if not defined BOTS_EXE if defined NVCOOP_REF_EXE set BOTS_EXE=%NVCOOP_REF_EXE%
set PATH=%PATH%;C:\Program Files (x86)\Microsoft Visual Studio\Installer
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cmake -S "%HERE%." -B "%BLD%" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl >nul || exit /b 1
cmake --build "%BLD%" || exit /b 1

"%BLD%\bots_tests\nvbots_tests_m0.exe" > "%BLD%\real.txt"
set REAL_RC=%ERRORLEVEL%
type "%BLD%\real.txt"
findstr /C:" 0 failed" "%BLD%\real.txt" >nul
set REAL_OK=%ERRORLEVEL%
set BAD=0
for /L %%N in (1,1,48) do (
  "%BLD%\bots_tests\nvbots_tests_m%%N.exe" > "%BLD%\mutant%%N.txt"
  set RC=!ERRORLEVEL!
  findstr /R /C:", [1-9][0-9]* failed" "%BLD%\mutant%%N.txt" >nul
  if !ERRORLEVEL! NEQ 0 ( set BAD=1 )
  if "!RC!"=="0" ( set BAD=1 )
  for /f "tokens=1,2" %%A in ('findstr /R /C:"checks, " "%BLD%\mutant%%N.txt"') do echo mutant %%N: %%A checks, caught by !RC!
)
if not "%REAL_RC%"=="0" exit /b 1
if not "%REAL_OK%"=="0" exit /b 1
if not "%BAD%"=="0" ( echo T6a bots: a mutant slipped through & exit /b 1 )
echo T6a bots: real tests pass, all 48 mutants rejected - ALL OK
exit /b 0
