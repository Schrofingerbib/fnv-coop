@echo off
rem All split-screen tests (focus guard, libraries, Python tools, engine pins). Prints ALL OK. Never starts the game.
if not defined PYTHON set "PYTHON=python"
"%PYTHON%" -I "%~dp0run_tests.py" %*
