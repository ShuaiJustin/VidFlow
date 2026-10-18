@echo off
chcp 65001 >nul
title VidFlow Desktop - Environment Setup
color 0A

REM Change to project root directory (one level up from scripts)
cd /d "%~dp0..\.."

echo.
echo ========================================
echo   VidFlow Desktop - Environment Setup
echo ========================================
echo.
echo This script will install:
echo   1. Node.js dependencies ^(Electron^)
echo   2. Frontend dependencies ^(React + Vite^)
echo   3. Python virtual environment
echo   4. Backend dependencies ^(FastAPI^)
echo   5. AI subtitle feature ^(faster-whisper, auto-install^)
echo.
echo Estimated time: 5-8 minutes
echo.
echo Note:
echo - faster-whisper requires Python 3.8 to 3.11 (NOT 3.12+)
echo - FFmpeg and yt-dlp will be auto-downloaded on first use
echo - Or place them manually in: backend\tools\bin\
echo.
pause

REM ========================================
REM Step 1: Check Environment
REM ========================================

echo.
echo ========================================
echo   [Step 1/6] Checking Environment
echo ========================================
echo.

REM Check Node.js
echo [Check] Node.js...
node --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo [ERROR] Node.js not found
    echo.
    echo Please install Node.js 18+ from: https://nodejs.org/
    echo.
    pause
    exit /b 1
)
for /f "tokens=*" %%i in ('node --version') do set NODE_VERSION=%%i
echo [OK] Node.js %NODE_VERSION%

REM Check Python 3.11 (Preferred)
echo [Check] Python 3.11 ^(Recommended^)...
py -3.11 --version >nul 2>&1
if errorlevel 1 (
    echo [WARN] Python 3.11 not found
    echo.
    echo Checking default Python...
    python --version >nul 2>&1
    if errorlevel 1 (
        color 0C
        echo [ERROR] Python not found
        echo.
        echo Please install Python 3.11 from: https://www.python.org/downloads/release/python-3119/
        echo Important: Check "Add Python to PATH" during installation
        echo.
        pause
        exit /b 1
    )
    for /f "tokens=*" %%i in ('python --version') do set PYTHON_VERSION=%%i
    echo [OK] %PYTHON_VERSION% ^(Using default Python^)
    echo [WARN] Recommend installing Python 3.11 for better compatibility

    REM Check if Python 3.12+ is being used
    echo %PYTHON_VERSION% | findstr /C:"Python 3.1[2-9]" >nul 2>&1
    if not errorlevel 1 (
        color 0E
        echo.
        echo ============================================================
        echo [WARNING] Python 3.12+ detected!
        echo ============================================================
        echo.
        echo faster-whisper AI subtitle needs Python 3.8-3.11
        echo Python 3.12+ is NOT compatible with faster-whisper
        echo.
        echo Recommendation:
        echo   - Install Python 3.11 for full feature support
        echo   - Download: https://www.python.org/downloads/release/python-3119/
        echo.
        echo You can continue but faster-whisper will NOT work.
        echo ============================================================
        echo.
        timeout /t 5 >nul
        color 0A
    )

    set PYTHON_CMD=python
) else (
    for /f "tokens=*" %%i in ('py -3.11 --version') do set PYTHON_VERSION=%%i
    echo [OK] %PYTHON_VERSION% ^(Using Python 3.11^)
    set PYTHON_CMD=py -3.11
)

echo.
echo [OK] All required tools are ready!
timeout /t 2 >nul

REM ========================================
REM Step 2: Install Root Dependencies
REM ========================================

echo.
echo ========================================
echo   [Step 2/6] Installing Electron
echo ========================================
echo.

if exist "node_modules" (
    echo [INFO] node_modules exists, skipping...
) else (
    echo [INFO] Installing Electron and build tools...
    echo.
    call npm install

    if errorlevel 1 (
        color 0C
        echo.
        echo [ERROR] Electron installation failed
        echo [TIP] Try: npm cache clean --force
        cd ..
        pause
        exit /b 1
    )

    echo.
    echo [OK] Electron installed
)

timeout /t 1 >nul

REM ========================================
REM Step 3: Install Frontend Dependencies
REM ========================================

echo.
echo ========================================
echo   [Step 3/6] Installing Frontend
echo ========================================
echo.

cd frontend

if exist "node_modules" (
    echo [INFO] Updating frontend dependencies...
) else (
    echo [INFO] Installing React, Vite, UI libraries...
    echo This may take 1-2 minutes...
)

echo.
call npm install

if errorlevel 1 (
    color 0C
    echo.
    echo [ERROR] Frontend installation failed
    echo [TIP] Check if npm is properly configured
    cd ..
    pause
    exit /b 1
)

cd ..

echo.
echo [OK] Frontend dependencies installed
timeout /t 1 >nul

REM ========================================
REM Step 4: Create Python Virtual Environment
REM ========================================

echo.
echo ========================================
echo   [Step 4/6] Creating Python venv
echo ========================================
echo.

cd backend

echo [INFO] Ensuring Python virtual environment with %PYTHON_CMD%...
echo.
%PYTHON_CMD% ..\scripts\dev\ensure_venv.py venv

if errorlevel 1 (
    color 0C
    echo.
    echo [ERROR] Failed to create virtual environment
    echo.
    echo If using Python 3.14+, try installing Python 3.11 instead
    cd ..
    pause
    exit /b 1
)

set VENV_PYTHON=venv\Scripts\python.exe
for /f "tokens=*" %%i in ('"%VENV_PYTHON%" --version 2^>^&1') do set VENV_PYTHON_VERSION=%%i
echo [OK] Virtual environment ready: %VENV_PYTHON_VERSION%

timeout /t 1 >nul

REM ========================================
REM Step 5: Install Python Dependencies
REM ========================================

echo.
echo ========================================
echo   [Step 5/6] Installing Backend
echo ========================================
echo.

echo [INFO] Verifying virtual environment interpreter...
if not exist "%VENV_PYTHON%" (
    color 0C
    echo.
    echo [ERROR] Virtual environment python not found
    cd ..
    pause
    exit /b 1
)

echo [OK] Using %VENV_PYTHON_VERSION%
echo.

echo [INFO] Upgrading pip...
"%VENV_PYTHON%" -m pip install --upgrade pip >nul 2>&1

echo [INFO] Installing FastAPI, SQLAlchemy, etc...
echo This may take 2-3 minutes...
echo.

"%VENV_PYTHON%" -m pip install -r requirements.txt

if errorlevel 1 (
    color 0C
    echo.
    echo [ERROR] Backend installation failed
    echo.
    echo Trying to install core packages...
    "%VENV_PYTHON%" -m pip install fastapi uvicorn[standard]
    "%VENV_PYTHON%" -m pip install sqlalchemy aiosqlite pydantic
    "%VENV_PYTHON%" -m pip install psutil aiohttp websockets yt-dlp requests
)

echo.
echo ========================================

REM AI subtitle feature notice (optional installation)
echo.
echo [5.5/6] AI Subtitle Feature Notice
echo ----------------------------------------
echo.
echo [INFO] AI subtitle feature is now optional
echo.
echo To use AI subtitle generation:
echo   1. Start the app, go to Settings - Tool Management
echo   2. Click "Install AI Subtitle Tool"
echo   3. Select version (CPU recommended, ~300 MB)
echo.
echo Benefits:
echo   - Smaller base package (~500 MB)
echo   - Install on demand
echo   - Can install/uninstall anytime
echo.
echo Checking Python compatibility...
echo %VENV_PYTHON_VERSION% | findstr /C:"Python 3.1[2-9]" >nul 2>&1
if not errorlevel 1 (
    echo   [WARN] Python %VENV_PYTHON_VERSION% NOT compatible with AI
    echo   [INFO] AI feature requires Python 3.8-3.11
    echo   [TIP] Rebuild venv with Python 3.11 if needed
) else (
    echo   [OK] Python version compatible: %VENV_PYTHON_VERSION%
    echo   [OK] AI feature can be installed in app
)
echo.

echo ========================================
cd ..

echo.
echo [OK] Backend dependencies installed
timeout /t 1 >nul

REM ========================================
REM Step 6: Verify Installation
REM ========================================

echo.
echo ========================================
echo   [Step 6/6] Verifying Installation
echo ========================================
echo.

echo [Check] Key packages...

if exist "frontend\node_modules\react" (
    echo [OK] React installed
) else (
    echo [WARN] React not found
)

if exist "frontend\node_modules\@radix-ui\react-checkbox" (
    echo [OK] Radix UI installed
) else (
    echo [WARN] Radix UI not found
)

cd backend

"%VENV_PYTHON%" -m pip show fastapi >nul 2>&1
if errorlevel 1 (
    echo [WARN] FastAPI not found
) else (
    echo [OK] FastAPI installed
)

"%VENV_PYTHON%" -m pip show psutil >nul 2>&1
if errorlevel 1 (
    echo [WARN] psutil not found
) else (
    echo [OK] psutil installed
)

cd ..

REM ========================================
REM Complete
REM ========================================

color 0A
echo.
echo ========================================
echo   Installation Complete!
echo ========================================
echo.
echo [OK] All dependencies installed
echo.
echo Next steps:
echo   1. Run: start.bat
echo   2. Or: npm run dev
echo.
echo Note: Backend uses dynamic port (configured in Electron)
echo Frontend UI: see frontend_port.json for actual port
echo.
echo ========================================
echo.

set /p START_NOW="Start development server now? (Y/N): "
if /i "%START_NOW%"=="Y" (
    echo.
    echo Starting...
    timeout /t 1 >nul
    cd /d "%~dp0"
    call start.bat
) else (
    echo.
    echo Run start.bat when ready
    echo.
    pause
)
