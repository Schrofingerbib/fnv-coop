@echo off
rem One click: 2 players, left and right. Extra options are passed on, e.g. --autoload MySave --monitor 2
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -I "%~dp0launch.py" up --players 2 --split lr %*
pause
