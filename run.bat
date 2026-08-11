@echo off
REM OWTracker - development launcher.
REM Uses the system Python and a local venv, and creates both on first run.
REM The launcher inside the downloadable release is a different file: it uses
REM the interpreter bundled beside it and never installs anything.

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
