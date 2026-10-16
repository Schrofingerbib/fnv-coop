@echo off
rem One command: build L1 artifacts (Win32) then run all L1 tests (python fake, C++ core, mutation twins).
setlocal
set "REPO=%~dp0..\.."
call "%~dp0build_l1.cmd" || exit /b 1
python -I -m unittest discover -s "%REPO%\tools\runner\tests" -v || exit /b 1
endlocal
