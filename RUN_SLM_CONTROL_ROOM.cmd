@echo off
setlocal
cd /d "%~dp0"
python -u -m training.launch_demo_control_room %*
exit /b %errorlevel%
