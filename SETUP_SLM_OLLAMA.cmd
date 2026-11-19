@echo off
setlocal
cd /d "%~dp0"
python -u -m training.install_demo_ollama
exit /b %errorlevel%
