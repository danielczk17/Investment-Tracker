@echo off
echo ============================================
echo  Investment Tracker — Build Script
echo ============================================
echo.

echo [1/3] Cleaning previous build folders...
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist
echo Done.
echo.

echo [2/3] Installing dependencies...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo ERROR: pip install failed. Make sure Python is installed and in PATH.
    pause
    exit /b 1
)

echo.
echo [3/3] Building executable...
pyinstaller investment_tracker.spec --noconfirm
if %errorlevel% neq 0 (
    echo ERROR: PyInstaller build failed. See output above for details.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  Build complete!
echo  Your app is in: dist\InvestmentTracker\
echo  Share the entire InvestmentTracker folder
echo  with your users.
echo ============================================
pause
