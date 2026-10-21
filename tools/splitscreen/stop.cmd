@echo off
rem Closes the split-screen game copies this launcher started (and nothing else).
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -I "%~dp0launch.py" down
pause
