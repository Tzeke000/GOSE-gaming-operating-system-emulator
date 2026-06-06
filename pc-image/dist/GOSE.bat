@echo off
rem GOSE — double-click to start. Boots the GOSE OS inside its own VM (your Windows is untouched).
title GOSE
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher\gose-launcher.ps1"
if errorlevel 1 (
  echo.
  echo GOSE could not start. See the messages above.
  pause
)
