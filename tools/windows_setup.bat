@echo off
setlocal DisableDelayedExpansion
set "PYTHONHOME="
set "PYTHONPATH="
set "PYTHONUTF8=1"
set "BILLING_PYTHON="
set "BILLING_SETUP_SCRIPT=%~dp0windows_setup.py"

rem Prefer a compatible launcher-managed interpreter; never install globally.
where py >nul 2>nul
if not errorlevel 1 (
    for %%V in (3.13 3.12 3.14 3.11 3.10) do (
        if not defined BILLING_PYTHON (
            py -%%V "%BILLING_SETUP_SCRIPT%" --check-python >nul 2>nul
            if not errorlevel 1 set "BILLING_PYTHON=py -%%V"
        )
    )
)
if not defined BILLING_PYTHON (
    python "%BILLING_SETUP_SCRIPT%" --check-python >nul 2>nul
    if not errorlevel 1 set "BILLING_PYTHON=python"
)
if not defined BILLING_PYTHON goto :missingpython

%BILLING_PYTHON% "%BILLING_SETUP_SCRIPT%" %*
set "BILLING_SETUP_RESULT=%errorlevel%"
if not defined BILLING_NO_PAUSE pause
exit /b %BILLING_SETUP_RESULT%

:missingpython
echo No compatible Python was found.
echo Install standard 64-bit CPython 3.10 through 3.14 for Windows x64.
echo Recommended: Python 3.13 with the Python launcher enabled.
echo Download: https://www.python.org/downloads/windows/
echo Then close this window and double-click setup.bat again.
if not defined BILLING_NO_PAUSE pause
exit /b 1
