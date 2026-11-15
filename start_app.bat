@echo off
title BGU Lecture Summarizer
color 0B

echo.
echo  ============================================================
echo   BGU Lecture Summarizer  v2.0  --  Next.js + FastAPI
echo  ============================================================
echo.

:: Resolve the project root (wherever this .bat lives)
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"

:: ── Sanity checks ─────────────────────────────────────────────────────────────
if not exist "%ROOT%\.venv\Scripts\uvicorn.exe" (
    echo  [ERROR] uvicorn not found in .venv.
    echo  Run:  .venv\Scripts\pip install -r backend\requirements.txt
    pause & exit /b 1
)

if not exist "%ROOT%\frontend\node_modules" (
    echo  [ERROR] Node modules not found.
    echo  Run:  cd frontend ^&^& npm install
    pause & exit /b 1
)

if not exist "%ROOT%\backend\.env" (
    echo  [WARN] backend\.env not found -- API key may be missing.
    echo  The app will fall back to the root .env file.
)

:: ── Kill any stale processes on ports 3000 and 8000 ──────────────────────────
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":3000 " ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTENING') do taskkill /PID %%a /F >nul 2>&1

:: ── Launch FastAPI backend in its own window ──────────────────────────────────
echo  Starting FastAPI backend on http://localhost:8000 ...
start "BGU Backend  [FastAPI:8000]" cmd /k ^
    "cd /d "%ROOT%\backend" && "%ROOT%\.venv\Scripts\uvicorn.exe" main:app --reload --port 8000 --log-level info"

:: Brief pause so backend starts first
timeout /t 2 /nobreak > nul

:: ── Launch Next.js frontend in its own window ─────────────────────────────────
echo  Starting Next.js frontend on http://localhost:3000 ...
start "BGU Frontend [Next.js:3000]" cmd /k ^
    "cd /d "%ROOT%\frontend" && npm run dev"

:: ── Open the browser after a short delay ─────────────────────────────────────
echo.
echo  Both servers are starting. Opening browser in 5 seconds...
echo  (Close the two server windows to stop the app)
echo.
timeout /t 5 /nobreak > nul
start "" "http://localhost:3000"

echo  App is running.
echo  - Frontend : http://localhost:3000
echo  - Backend  : http://localhost:8000
echo  - API docs : http://localhost:8000/docs
echo.
pause
