@echo off
setlocal EnableExtensions
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0publish-github-release.ps1" %*
exit /b %ERRORLEVEL%
