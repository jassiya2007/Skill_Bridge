@echo off
cd /d "%~dp0backend"
where python >nul 2>nul
if not errorlevel 1 (
    set "PYTHON=python"
) else (
    where py >nul 2>nul
    if errorlevel 1 (
        echo Python 3 was not found. Install it from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set "PYTHON=py -3"
)

%PYTHON% -m venv .venv
if errorlevel 1 (
    echo Could not create the virtual environment. Ensure Python 3 is installed correctly.
    pause
    exit /b 1
)
call .venv\Scripts\activate
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
)
python main.py
