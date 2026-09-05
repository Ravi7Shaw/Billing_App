@echo off
title Cloth Shop Billing Server
cd /d "%~dp0"

REM First-time setup: create a virtual environment and install dependencies
REM if they aren't already installed. Safe to leave in -- it's instant on
REM every run after the first.
if not exist "venv" (
    echo Setting up for the first time, this may take a minute...
    python -m venv venv
    call venv\Scripts\activate.bat
    pip install --quiet -r backend\requirements.txt
) else (
    call venv\Scripts\activate.bat
)

python run_server.py

echo.
echo Server stopped.
pause
