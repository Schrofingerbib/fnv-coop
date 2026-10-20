@echo off
rem One-command L11 tests: standalone build in %TEMP%\nvui_build, then
rem   real tests   must report "0 failed"      (and exit 0)
rem   every mutant (NVUI_MUTANT=1..9) must report ">0 failed" (and exit non-zero)
rem Set NVUI_EXE=<dump of the running game image> to also compare every pinned prologue with the real image.
setlocal EnableDelayedExpansion
set HERE=%~dp0
set BLD=%TEMP%\nvui_build
set CXX=
set CC=
set CMAKE_TOOLCHAIN_FILE=
set CMAKE_GENERATOR=
set CMAKE_ANDROID_NDK=
set PATH=%PATH%;C:\Program Files (x86)\Microsoft Visual Studio\Installer
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
cmake -S "%HERE%." -B "%BLD%" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl >nul || exit /b 1
cmake --build "%BLD%" || exit /b 1

"%BLD%\ui_tests\nvui_tests_m0.exe" > "%BLD%\real.txt"
set REAL_RC=%ERRORLEVEL%
type "%BLD%\real.txt"
findstr /C:" 0 failed" "%BLD%\real.txt" >nul
set REAL_OK=%ERRORLEVEL%
set BAD=0
for %%N in (1 2 3 4 5 6 7 8 9) do (
  "%BLD%\ui_tests\nvui_tests_m%%N.exe" > "%BLD%\mutant%%N.txt"
  set RC=!ERRORLEVEL!
  findstr /R /C:", [1-9][0-9]* failed" "%BLD%\mutant%%N.txt" >nul
  if !ERRORLEVEL! NEQ 0 ( set BAD=1 )
  if "!RC!"=="0" ( set BAD=1 )
  for /f "tokens=1,2" %%A in ('findstr /R /C:"checks, " "%BLD%\mutant%%N.txt"') do echo mutant %%N: %%A checks, caught by !RC!
)
rem ---- production wiring + slideshow (bridge tests): real must pass, mutants 10..16 must fail
"%BLD%\ui_tests\nvui_bridge_tests_m0.exe" > "%BLD%\bridge_real.txt"
set BRIDGE_RC=%ERRORLEVEL%
type "%BLD%\bridge_real.txt"
findstr /C:" 0 failed" "%BLD%\bridge_real.txt" >nul
set BRIDGE_OK=%ERRORLEVEL%
for %%N in (10 11 12 13 14 15 16) do (
  "%BLD%\ui_tests\nvui_bridge_tests_m%%N.exe" > "%BLD%\bmutant%%N.txt"
  set RC=!ERRORLEVEL!
  findstr /R /C:", [1-9][0-9]* failed" "%BLD%\bmutant%%N.txt" >nul
  if !ERRORLEVEL! NEQ 0 ( set BAD=1 )
  if "!RC!"=="0" ( set BAD=1 )
  for /f "tokens=1,2" %%A in ('findstr /R /C:"checks, " "%BLD%\bmutant%%N.txt"') do echo bridge mutant %%N: %%A checks, caught by !RC!
)
if not "%BRIDGE_RC%"=="0" exit /b 1
if not "%BRIDGE_OK%"=="0" exit /b 1
echo real rc=%REAL_RC% zero-failed-line=%REAL_OK%  mutants-all-caught=%BAD%(0 = yes)
if not "%REAL_RC%"=="0" exit /b 1
if not "%REAL_OK%"=="0" exit /b 1
if not "%BAD%"=="0" exit /b 1
echo L11 hooks_ui: real tests pass, all 16 mutants rejected - ALL OK
exit /b 0
