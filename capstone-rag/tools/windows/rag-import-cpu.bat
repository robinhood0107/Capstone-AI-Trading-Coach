@echo off
setlocal
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0rag-content.ps1" -Command import-cpu %*
exit /b %ERRORLEVEL%
