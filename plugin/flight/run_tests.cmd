@echo off
rem T1a plugin side: build (x86, 2 jobs) the Service + runtime tests and their wiring twins, run them. Prints ALL OK when everything passes.
rem Also runs the synccore recorder tests (synccore\flight\run_tests.bat).
setlocal
set "CMAKE_TOOLCHAIN_FILE="
set "ANDROID_NDK_HOME="
set "CXX="
set "CC="
set CMAKE_BUILD_PARALLEL_LEVEL=2
set "REPO=%~dp0..\.."
set "PATH=C:\Program Files (x86)\Microsoft Visual Studio\Installer;%PATH%"
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat" >nul || exit /b 1
set "PATH=C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja;%PATH%"
call "%REPO%\synccore\flight\run_tests.bat" || exit /b 1
cmake -S "%~dp0." -B "%REPO%\build\T1a-plugin" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl || exit /b 1
cmake --build "%REPO%\build\T1a-plugin" || exit /b 1
if not exist "%REPO%\out\T1a" mkdir "%REPO%\out\T1a"
echo === flight_runtime_tests ===
set "FNVCOOP_FLIGHT_KEEP=1"
"%REPO%\out\T1a\flight_runtime_tests.exe" "%REPO%\out\T1a\tmp_real" || exit /b 1
set "FNVCOOP_FLIGHT_KEEP="
python -I "%~dp0tests\check_runtime_traces.py" "%REPO%\out\T1a\tmp_real" || exit /b 1
for %%V in (1 2 3) do (
  echo === twin %%V: must FAIL ===
  "%REPO%\out\T1a\flight_runtime_tests_mut%%V.exe" "%REPO%\out\T1a\tmp_mut%%V" >nul 2>&1
  if not errorlevel 1 ( echo TWIN %%V WAS NOT CAUGHT & exit /b 1 )
)
echo flight plugin: ALL OK
endlocal
