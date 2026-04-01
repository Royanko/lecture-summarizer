@echo off
title BGU Lecture Summarizer
color 0A

echo.
echo  ==========================================
echo   BGU Lecture Summarizer - Starting up...
echo  ==========================================
echo.

:: Move to the project directory (wherever this .bat lives)
cd /d "%~dp0"

:: Activate the virtual environment
if not exist ".venv\Scripts\activate.bat" (
    echo  [ERROR] .venv not found in: %~dp0
    echo  Make sure the .venv folder exists in the project root.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

:: Launch Streamlit
echo  Launching app at http://localhost:8501
echo  Press Ctrl+C in this window to stop the server.
echo.
streamlit run app.py

:: Keep window open if streamlit exits unexpectedly
echo.
echo  [Server stopped]
pause
