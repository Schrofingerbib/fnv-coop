@echo off
rem One-command split-screen tests (focus guard): x86 standalone build in build\splitscreen (inside the repo, not %%TEMP%%),
rem   real tests must report " 0 failed"; every mutant (FOCUS_GUARD_MUTANT=1..6) must report ", N failed" with N > 0.
setlocal EnableDelayedExpansion
set HERE=%~dp0
set BLD=%HERE%..\..\build\splitscreen
set CXX=
set CC=
set CMAKE_TOOLCHAIN_FILE=
set CMAKE_GENERATOR=
if not defined CMAKE_BUILD_PARALLEL_LEVEL set CMAKE_BUILD_PARALLEL_LEVEL=2
set PATH=%PATH%;C:\Program Files (x86)\Microsoft Visual Studio\Installer
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat" >nul || exit /b 1
cmake -S "%HERE%." -B "%BLD%" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl >nul || exit /b 1
cmake --build "%BLD%" || exit /b 1

"%BLD%\nvsplit_focus_tests_m0.exe" > "%BLD%\real.txt"
set REAL_RC=%ERRORLEVEL%
type "%BLD%\real.txt"
findstr /C:" 0 failed" "%BLD%\real.txt" >nul
set REAL_OK=%ERRORLEVEL%
set BAD=0
for /L %%N in (1,1,6) do (
  "%BLD%\nvsplit_focus_tests_m%%N.exe" > "%BLD%\mutant%%N.txt"
  set RC=!ERRORLEVEL!
  findstr /R /C:", [1-9][0-9]* failed" "%BLD%\mutant%%N.txt" >nul
  if !ERRORLEVEL! NEQ 0 ( set BAD=1 & echo mutant %%N SURVIVED )
  if "!RC!"=="0" ( set BAD=1 & echo mutant %%N exited 0 )
  for /f "delims=" %%L in ('findstr /C:"checks, " "%BLD%\mutant%%N.txt"') do echo mutant %%N: %%L
)
if not "%REAL_RC%"=="0" exit /b 1
if not "%REAL_OK%"=="0" exit /b 1
if not "%BAD%"=="0" ( echo splitscreen: a mutant slipped through & exit /b 1 )
echo splitscreen focus guard: real tests pass, all 6 mutants rejected - ALL OK
exit /b 0
