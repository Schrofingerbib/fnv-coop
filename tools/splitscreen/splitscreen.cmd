@echo off
rem Split-screen menu for Fallout: New Vegas co-op. Double-click, pick a number.
setlocal
if not defined PYTHON set "PYTHON=python"
set "L=%PYTHON% -I "%~dp0launch.py""
:menu
echo.
echo Fallout: New Vegas co-op - split-screen on this PC
echo   1  2 players, left and right
echo   2  2 players, top and bottom
echo   3  3 players (quad layout)
echo   4  4 players (quad layout)
echo   5  check the layout for the primary monitor
echo   6  close the split-screen games
echo   Q  quit
choice /c 123456Q /n /m "Choose: "
if errorlevel 7 exit /b 0
if errorlevel 6 goto stop
if errorlevel 5 goto check
if errorlevel 4 goto p4
if errorlevel 3 goto p3
if errorlevel 2 goto p2tb
%L% up --players 2 --split lr %*
goto menu
:p2tb
%L% up --players 2 --split tb %*
goto menu
:p3
%L% up --players 3 %*
goto menu
:p4
%L% up --players 4 %*
goto menu
:check
%L% plan --players 2
%PYTHON% -I "%~dp0check_layout.py" --players 2
%PYTHON% -I "%~dp0check_layout.py" --players 4
goto menu
:stop
%L% down
goto menu
