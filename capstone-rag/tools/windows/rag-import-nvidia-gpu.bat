@echo off
setlocal
if not "%~1"=="" (
  echo {"code":"CONTENT_COMMAND_INVALID","state":"FAILED"}
  exit /b 2
)
powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0rag-content.ps1" -Command import-nvidia-gpu
exit /b %ERRORLEVEL%
