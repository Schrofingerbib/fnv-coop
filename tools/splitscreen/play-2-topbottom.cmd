@echo off
rem One click: 2 players, top and bottom.
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -I "%~dp0launch.py" up --players 2 --split tb %*
pause
