@echo off
rem Builds the full nvcoop.dll (L1 plugin + L7 snapshot + L5 hooks_module) into out\L5\ without touching out\L1.
setlocal
set ROOT=%~dp0..\..
set CXX=
set CC=
set CMAKE_TOOLCHAIN_FILE=
set CMAKE_GENERATOR=
set PATH=%PATH%;C:\Program Files (x86)\Microsoft Visual Studio\Installer
call "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat" >nul || exit /b 1
cmake -S "%ROOT%\plugin" -B "%ROOT%\build\L5" -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_COMPILER=cl -DNVCOOP_OUT_DIR="%ROOT%\out\L5" >nul || exit /b 1
cmake --build "%ROOT%\build\L5" --target nvcoop || exit /b 1
exit /b 0
