@echo off
:: AstroNightOrganizer launcher — double-click this to start the app
:: Uses uv to manage the Python environment automatically

where uv >nul 2>&1
if errorlevel 1 (
    echo.
    echo ============================================================
    echo   AstroNightOrganizer requires 'uv' to be installed.
    echo   Install it from:  https://docs.astral.sh/uv/
    echo   Or via PowerShell:
    echo     winget install astral-sh.uv
    echo ============================================================
    echo.
    pause
    exit /b 1
)

cd /d "%~dp0"
uv run python run.py
