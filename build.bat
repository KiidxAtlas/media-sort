@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 exit /b %errorlevel%

uv --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: uv is not installed or not in PATH.
    echo Install uv from https://docs.astral.sh/uv/getting-started/installation/
    exit /b 1
)

uv sync --locked
if errorlevel 1 exit /b %errorlevel%

uv run --locked build.py
exit /b %errorlevel%
