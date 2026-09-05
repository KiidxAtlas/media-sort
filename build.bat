@echo off
setlocal enabledelayedexpansion

echo ================================
echo  MediaSort — Build Windows .exe
echo ================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Install Python from https://python.org and check "Add to PATH"
    pause
    exit /b 1
)

REM Check if uv is installed
uv --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: uv is not installed.
    echo Install with: pip install uv
    pause
    exit /b 1
)

echo [1/3] Installing dependencies...
uv sync
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)
echo.

echo [2/3] Building executable with PyInstaller...
uv run pyinstaller --clean --noconfirm build.spec
if errorlevel 1 (
    echo ERROR: Build failed.
    pause
    exit /b 1
)
echo.

echo [3/3] Done!
echo.
echo Output: dist\MediaSort.exe
echo Run it by double-clicking or from command line:
echo   dist\MediaSort.exe
echo.
pause
