@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title Factsheet Tool - First-time Setup
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
if not defined FACTSHEET_STATE_ROOT set "FACTSHEET_STATE_ROOT=%~dp0..\state"
if not defined FACTSHEET_RUNTIME_ROOT set "FACTSHEET_RUNTIME_ROOT=%FACTSHEET_STATE_ROOT%\runtime"
set "PYTHON_EXE=%FACTSHEET_RUNTIME_ROOT%\python\python.exe"
set "PLAYWRIGHT_BROWSERS_PATH=%FACTSHEET_RUNTIME_ROOT%\ms-playwright"

if not exist "%PYTHON_EXE%" (
    echo [!] The offline package is incomplete: portable Python is missing.
    echo     Missing: %PYTHON_EXE%
    echo.
    echo Ask the delivery owner to rebuild the external runtime and copy the code/state package.
    echo.
    pause
    exit /b 2
)

if not exist "%FACTSHEET_RUNTIME_ROOT%\wheels" (
    echo [!] The offline wheel folder %FACTSHEET_RUNTIME_ROOT%\wheels is missing.
    pause
    exit /b 2
)

echo [1/2] Checking and repairing Python from offline wheels...
"%PYTHON_EXE%" -m pip install --disable-pip-version-check --no-index --find-links "%FACTSHEET_RUNTIME_ROOT%\wheels" -r "%~dp0requirements.lock.txt"
if errorlevel 1 (
    echo.
    echo [!] Offline dependency installation failed.
    pause
    exit /b 2
)
"%PYTHON_EXE%" -m pip install --disable-pip-version-check --no-index --find-links "%FACTSHEET_RUNTIME_ROOT%\wheels" -r "%~dp0platform\requirements.lock.txt"
if errorlevel 1 (
    echo.
    echo [!] Offline platform dependency installation failed.
    pause
    exit /b 2
)

echo.
echo [2/2] Validating config and the portable browser...
"%PYTHON_EXE%" -u code\setup_environment.py --require-portable
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (
    echo Setup completed. You do not need to run this file again.
) else (
    echo Setup did not complete. Follow the message above.
)
pause
exit /b %EXIT_CODE%
