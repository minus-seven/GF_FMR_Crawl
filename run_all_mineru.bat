@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
if not defined FACTSHEET_STATE_ROOT set "FACTSHEET_STATE_ROOT=%~dp0..\state"
if not defined FACTSHEET_RUNTIME_ROOT set "FACTSHEET_RUNTIME_ROOT=%FACTSHEET_STATE_ROOT%\runtime"
set "PYTHON_EXE=%FACTSHEET_RUNTIME_ROOT%\python\python.exe"
if exist "%PYTHON_EXE%" set "PLAYWRIGHT_BROWSERS_PATH=%FACTSHEET_RUNTIME_ROOT%\ms-playwright"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%FACTSHEET_STATE_ROOT%\dependencies\venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"
if not "%~1"=="" goto run_with_args

"%PYTHON_EXE%" -u code\run_with_config.py run_all_mineru.py --select-batch
set "EXIT_CODE=%ERRORLEVEL%"
echo.
pause
exit /b %EXIT_CODE%

:run_with_args
"%PYTHON_EXE%" -u code\run_with_config.py run_all_mineru.py %*
exit /b %ERRORLEVEL%
