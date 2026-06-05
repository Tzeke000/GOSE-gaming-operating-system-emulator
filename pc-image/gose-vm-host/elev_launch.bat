@echo off
REM GOSE elevated agent launcher — double-click (or run) ONCE; approve the single UAC prompt.
REM This starts the elevated agent hidden; Wren then controls it via D:\gose-vm\elev\ and kills it when done.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile','-WindowStyle','Hidden','-ExecutionPolicy','Bypass','-File','D:\gose-vm\elev_agent.ps1'"
