@echo off
REM ---------------------------------------------------------------
REM  AR Beat Saber - local launcher
REM
REM  The browser refuses camera access on file:// URLs, so the game
REM  has to be served. http://localhost counts as a secure context,
REM  so a plain static server is all that is needed - still fully
REM  offline, nothing leaves the machine.
REM ---------------------------------------------------------------

cd /d "%~dp0"

set PORT=8000

echo.
echo   AR BEAT SABER - starting local server on port %PORT%
echo   Leave this window open while you play. Ctrl+C to stop.
echo.

start "" http://localhost:%PORT%/index.html

python -m http.server %PORT% 2>nul
if errorlevel 1 (
  echo.
  echo   Python not found. Trying Node instead...
  npx --yes http-server -p %PORT% -c-1
)

pause
