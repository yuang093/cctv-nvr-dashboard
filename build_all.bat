@echo off
REM build_all.bat
REM ==============
REM Convenience: build web.exe (single exe)。
REM (worker.exe removed 2026-07-03 — user wants scan triggered from Web UI button.)

setlocal EnableExtensions

set "PROJECT_DIR=%~dp0"

echo ============================================
echo [1/1] Building web.exe ...
echo ============================================
call "%PROJECT_DIR%build_web.bat"
if errorlevel 1 (
    echo [FATAL] build_web.bat failed, aborting 1>&2
    exit /b 1
)

echo.
echo ============================================
echo [OK] Build complete.
echo   - dist\web\web.exe        (Flask Web UI launcher)
echo.
echo [NEXT] Test it:
echo   .\dist\web\web.exe
echo ============================================
exit /b 0
