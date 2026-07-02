@echo off
REM Build the Vue 3 / Vite frontend for production.
REM Output lands in code/frontend/dist/ — copy to server or let Docker pick it up.

cd /d "%~dp0..\code\frontend"
call npm run build
if errorlevel 1 (
    echo [ERROR] Frontend build failed.
    exit /b 1
)
echo [OK] Frontend built → code/frontend/dist/
