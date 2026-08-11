@echo off
REM OWTracker - development launcher.
REM Uses the system Python and a local venv. The SHIPPED launcher is different:
REM tools\package.py generates one that uses the bundled interpreter and never
REM installs anything. See CLAUDE-OWTRACKER.md, milestone 8.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [owtracker] creating virtual environment...
    python -m venv .venv || goto :error
    ".venv\Scripts\python.exe" -m pip install --upgrade pip --quiet || goto :error
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :error
)

".venv\Scripts\python.exe" -m app.main
goto :eof

:error
echo.
echo [owtracker] setup failed. Is Python 3.12+ installed and on PATH?
pause
exit /b 1
