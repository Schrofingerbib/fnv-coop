@echo off
rem One click: 3 players in the quad layout.
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -I "%~dp0launch.py" up --players 3 %*
pause
