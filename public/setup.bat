@echo off
title Starlight RDT Setup
color 0B
echo.
echo  ============================================
echo   Starlight RDT - Windows Agent Setup
echo  ============================================
echo.

:: Check Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Python is not installed.
    echo.
    echo  Please download Python from:
    echo  https://www.python.org/downloads/
    echo.
    echo  During install, make sure to check:
    echo  "Add Python to PATH"
    echo.
    pause
    start https://www.python.org/downloads/
    exit /b 1
)

echo  [OK] Python found
echo.
echo  Installing dependencies (this takes ~30 seconds)...
echo.

pip install mss pyautogui websockets Pillow pystray pyperclip --quiet --disable-pip-version-check

if errorlevel 1 (
    echo.
    echo  [ERROR] Failed to install dependencies.
    echo  Try running this as Administrator.
    pause
    exit /b 1
)

echo.
echo  [OK] Dependencies installed
echo.
echo  ============================================
echo   Starting Starlight RDT Agent...
echo  ============================================
echo.
echo  Your session code will appear in a moment.
echo  Share it at: https://nicepeople67.github.io/Starlight-RDT/login.html
echo.
echo  Keep this window open while sharing your screen.
echo  Close it to stop sharing.
echo.

python agent\agent.py

pause