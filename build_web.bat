@echo off
setlocal EnableExtensions
set "PD=%~dp0"
set "VENV=%PD%venv\Scripts\python.exe"
if not exist "%VENV%" ( echo [FATAL] venv missing & exit /b 1 )
"%VENV%" -c "import PyInstaller" 2>nul
if errorlevel 1 ( "%VENV%" -m pip install "pyinstaller>=6.0" & if errorlevel 1 ( echo [FATAL] & exit /b 2 ) )
cd /d "%PD%"
if exist "%PD%nvr_scan.db" (
  echo [INFO] Building web.exe with DB...
  "%VENV%" -m PyInstaller --noconfirm --clean --name web --console --onedir --add-data "%PD%web\templates;web\templates" --add-data "%PD%web\static;web\static" --add-data "%PD%nvr_config.json;." --add-data "%PD%nvr_scan.db;." --collect-all flask --collect-all jinja2 --collect-all click --collect-all itsdangerous --collect-all markupsafe --collect-all werkzeug --collect-all reportlab --collect-all tzdata --collect-all requests --collect-all urllib3 --collect-all certifi --collect-all charset_normalizer --collect-all idna --hidden-import sqlite3 --hidden-import hashlib --hidden-import json --hidden-import csv "%PD%web\app.py"
) else (
  echo [INFO] Building web.exe without DB...
  "%VENV%" -m PyInstaller --noconfirm --clean --name web --console --onedir --add-data "%PD%web\templates;web\templates" --add-data "%PD%web\static;web\static" --add-data "%PD%nvr_config.json;." --collect-all flask --collect-all jinja2 --collect-all click --collect-all itsdangerous --collect-all markupsafe --collect-all werkzeug --collect-all reportlab --collect-all tzdata --collect-all requests --collect-all urllib3 --collect-all certifi --collect-all charset_normalizer --collect-all idna --hidden-import sqlite3 --hidden-import hashlib --hidden-import json --hidden-import csv "%PD%web\app.py"
)
if errorlevel 1 ( echo [FATAL] & exit /b 4 )
echo.
echo [OK] Built: dist\web\web.exe
exit /b 0
