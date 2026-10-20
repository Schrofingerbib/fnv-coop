@echo off
rem Build (x86, Ninja) and run the game-free loader tests + mutation twins. Prints ALL OK on success.
rem Usage: plugin\loader\run_tests.cmd [clean]
setlocal
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
set "BLD=%REPO%\build\T3a"
if /i "%1"=="clean" rmdir /s /q "%BLD%" 2>nul
cmake -S "%HERE%." -B "%BLD%" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl >nul || (echo CONFIGURE FAILED & exit /b 1)
cmake --build "%BLD%" || (echo BUILD FAILED & exit /b 1)
set "OUT=%REPO%\out\T3a"
"%OUT%\nvloader_test.exe" "%OUT%" || (echo TESTS FAILED & exit /b 1)
for %%M in (nocopy nopurge noveto norollback noconfig configonce noprune noattrs novolatile coredirconfig retryall noretry) do call :mutant %%M || exit /b 1
echo ALL OK
endlocal
exit /b 0

:mutant
"%OUT%\nvloader_test_%1.exe" "%OUT%" >nul 2>&1
if "%errorlevel%"=="0" (echo MUTANT %1 SURVIVED & exit /b 1)
echo mutant %1 caught
exit /b 0
