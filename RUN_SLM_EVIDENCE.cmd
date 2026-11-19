@echo off
setlocal
cd /d "%~dp0"
python -m training.run_evidence_experiment %*
exit /b %ERRORLEVEL%
