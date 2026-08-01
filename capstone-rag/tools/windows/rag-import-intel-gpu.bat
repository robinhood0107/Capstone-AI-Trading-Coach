@echo off
setlocal
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0rag-content.ps1" -Command import-intel-gpu %*
exit /b %ERRORLEVEL%
