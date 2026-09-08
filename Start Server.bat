@echo off
title Taste of Ethiopia - Server
echo Checking if the server is already running...

rem If something already answers on port 8000, don't start a second copy -
rem just open the site in the browser and exit.
curl -s -m 3 -o nul http://localhost:8000/api/menu
if %errorlevel%==0 (
    echo.
    echo Server is ALREADY running - opening the website...
    start http://localhost:8000
    timeout /t 3 >nul
    exit /b 0
)

echo Not running yet - starting the server...
echo.
cd /d "%~dp0backend"
"C:\Users\Ephre\AppData\Local\Microsoft\WindowsApps\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
echo.
echo Server stopped or failed to start. If you saw a socket/permission error,
echo the port may be taken by another program - close other server windows first.
pause
