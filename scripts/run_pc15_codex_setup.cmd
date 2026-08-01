@echo off
set "SETUP_SCRIPT=C:\project mae share\MAE Progress Handoffs\SETUP_PC15_CODEX_ACCESS.ps1"

if not exist "%SETUP_SCRIPT%" (
  echo Setup script was not found:
  echo %SETUP_SCRIPT%
  pause
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File ""C:\project mae share\MAE Progress Handoffs\SETUP_PC15_CODEX_ACCESS.ps1""'"

if errorlevel 1 (
  echo Windows could not start the administrator setup window.
  pause
  exit /b 1
)

echo Approve the Windows administrator prompt, then follow the new PowerShell window.
timeout /t 5 /nobreak >nul
