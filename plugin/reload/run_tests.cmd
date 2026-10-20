@echo off
rem Build (x86, Ninja) and run the reload state tests + mutation twins. Prints ALL OK on success.
rem   1. registry unit tests (real must pass; NVRELOAD_MUTANT 1..8 must each fail)
rem   2. host-process test: 100 unload/reload cycles of a real test core (real must pass; TESTCORE_MUTANT 1..9 must each fail)
rem   3. the same host test against loader twins noconfig / configonce (config not copied to the cache): each must fail
rem Usage: plugin\reload\run_tests.cmd [clean]
setlocal EnableDelayedExpansion
set "CMAKE_TOOLCHAIN_FILE="
set "ANDROID_NDK_HOME="
set "CXX="
set "CC="
set "CMAKE_BUILD_PARALLEL_LEVEL=2"
set "HERE=%~dp0"
set "REPO=%HERE%..\.."
set "PATH=C:\Program Files (x86)\Microsoft Visual Studio\Installer;%PATH%"
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat" >nul || exit /b 1
set "PATH=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
set "BLD=%REPO%\build\T3b"
if /i "%1"=="clean" rmdir /s /q "%BLD%" 2>nul
cmake -S "%HERE%." -B "%BLD%" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl >nul || (echo CONFIGURE FAILED & exit /b 1)
cmake --build "%BLD%" || (echo BUILD FAILED & exit /b 1)
set "OUT=%REPO%\out\T3b"
set "SCR=%REPO%\out\T3b\tmp"
if not exist "%SCR%" mkdir "%SCR%"

"%OUT%\nvreload_unit_m0.exe" > "%SCR%\unit_real.txt"
set RC=!ERRORLEVEL!
type "%SCR%\unit_real.txt"
if not "!RC!"=="0" (echo UNIT TESTS FAILED & exit /b 1)
findstr /C:", 0 failed" "%SCR%\unit_real.txt" >nul || (echo UNIT TESTS: no zero-failed line & exit /b 1)
for %%N in (1 2 3 4 5 6 7 8) do (
  "%OUT%\nvreload_unit_m%%N.exe" >nul 2>&1
  if not errorlevel 1 (echo UNIT MUTANT %%N SURVIVED & exit /b 1)
  echo unit mutant %%N caught
)

"%OUT%\nvreload_host_test.exe" "%OUT%\nvtestcore_m0.dll" "%SCR%\host_real" > "%SCR%\host_real.txt"
set RC=!ERRORLEVEL!
type "%SCR%\host_real.txt"
if not "!RC!"=="0" (echo HOST TEST FAILED & exit /b 1)
findstr /C:", 0 failed" "%SCR%\host_real.txt" >nul || (echo HOST TEST: no zero-failed line & exit /b 1)
for %%N in (1 2 3 4 5 6 7 8 9) do (
  "%OUT%\nvreload_host_test.exe" "%OUT%\nvtestcore_m%%N.dll" "%SCR%\host_m%%N" >nul 2>&1
  if not errorlevel 1 (echo HOST MUTANT %%N SURVIVED & exit /b 1)
  echo host mutant %%N caught
)
for %%M in (noconfig configonce) do (
  "%OUT%\nvreload_host_test_%%M.exe" "%OUT%\nvtestcore_m0.dll" "%SCR%\host_%%M" > "%SCR%\host_%%M.txt" 2>&1
  if not errorlevel 1 (echo HOST LOADER MUTANT %%M SURVIVED & exit /b 1)
  findstr /C:"read the ini" /C:"read nvcoop.ini" "%SCR%\host_%%M.txt" >nul || (echo HOST LOADER MUTANT %%M failed for another reason & exit /b 1)
  echo host loader mutant %%M caught
)
echo ALL OK
endlocal
