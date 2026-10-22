@echo off
setlocal DisableDelayedExpansion
if not exist "%~dp0tools\windows_setup.bat" (
    echo Setup files are missing. Download or pull the complete repository.
    if not defined BILLING_NO_PAUSE pause
    exit /b 1
)
call "%~dp0tools\windows_setup.bat" --app-dir "%~dp0Billing_App\app" %*
exit /b %errorlevel%
