@echo off
REM MindContinuum local launcher - Windows 10/11
REM
REM Creates a venv, installs deps, then runs the server.
REM Storage defaults to %LOCALAPPDATA%\MindContinuum\mindcontinuum.sqlite.
REM Override with: set MINDCONTINUUM_DATA_DIR=C:\path\to\folder

setlocal ENABLEEXTENSIONS ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

where py >NUL 2>&1
if %ERRORLEVEL%==0 (
    set "PYLAUNCHER=py -3"
) else (
    where python >NUL 2>&1
    if %ERRORLEVEL%==0 (
        set "PYLAUNCHER=python"
    ) else (
        echo [mindcontinuum] ERROR: Python 3.11+ not found.
        echo [mindcontinuum] Install from https://www.python.org/downloads/windows/
        exit /b 1
    )
)

if not exist ".venv" (
    echo [mindcontinuum] creating virtual environment .venv
    %PYLAUNCHER% -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
    echo [mindcontinuum] ERROR: venv created but python.exe missing in .venv\Scripts
    exit /b 1
)

set "VENV_PY=%CD%\.venv\Scripts\python.exe"
set "VENV_PIP=%CD%\.venv\Scripts\pip.exe"

if not exist ".venv\.installed" (
    echo [mindcontinuum] installing dependencies (first run)
    "%VENV_PY%" -m pip install --quiet --upgrade pip
    "%VENV_PIP%" install --quiet -e .
    if errorlevel 1 (
        echo [mindcontinuum] ERROR: pip install failed
        exit /b 1
    )
    type nul > ".venv\.installed"
)

echo.
echo ========================================
echo  MindContinuum - local memory server
echo  UI/API : http://127.0.0.1:3780/
echo  MCP    : http://127.0.0.1:3780/mcp
echo  Ctrl+C to stop
echo ========================================
echo.

"%VENV_PY%" -m mindcontinuum %*
endlocal
