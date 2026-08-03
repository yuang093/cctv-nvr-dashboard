@echo off
setlocal EnableExtensions
set "PD=%~dp0"
set "VENV=%PD%venv\Scripts\python.exe"
if not exist "%VENV%" ( echo [FATAL] venv missing & exit /b 1 )
"%VENV%" -c "import PyInstaller" 2>nul
if errorlevel 1 ( "%VENV%" -m pip install "pyinstaller>=6.0" & if errorlevel 1 ( echo [FATAL] & exit /b 2 ) )
cd /d "%PD%"
if exist "%PD%nvr_scan.db" (
  echo [INFO] Building clips.exe with DB...
  "%VENV%" -m PyInstaller --noconfirm --clean --name clips --console --onedir --add-data "%PD%web\clips_templates;web\clips_templates" --add-data "%PD%nvr_config.json;." --add-data "%PD%nvr_scan.db;." --collect-all flask --collect-all jinja2 --collect-all click --collect-all itsdangerous --collect-all markupsafe --collect-all werkzeug --collect-all requests --collect-all urllib3 --collect-all certifi --collect-all charset_normalizer --collect-all idna --collect-all Pillow --collect-all tzdata --hidden-import sqlite3 --hidden-import hashlib --hidden-import json --hidden-import csv --hidden-import PIL.Image "%PD%web\clips_app.py"
) else (
  echo [INFO] Building clips.exe without DB...
  "%VENV%" -m PyInstaller --noconfirm --clean --name clips --console --onedir --add-data "%PD%web\clips_templates;web\clips_templates" --add-data "%PD%nvr_config.json;." --collect-all flask --collect-all jinja2 --collect-all click --collect-all itsdangerous --collect-all markupsafe --collect-all werkzeug --collect-all requests --collect-all urllib3 --collect-all certifi --collect-all charset_normalizer --collect-all idna --collect-all Pillow --collect-all tzdata --hidden-import sqlite3 --hidden-import hashlib --hidden-import json --hidden-import csv --hidden-import PIL.Image "%PD%web\clips_app.py"
)
if errorlevel 1 ( echo [FATAL] & exit /b 4 )
echo.
echo [OK] Built: dist\clips\clips.exe
exit /b 0
