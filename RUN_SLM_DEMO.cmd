@echo off
setlocal
if not "%~1"=="" set "SCRAM_DEMO_INPUT=%~f1"
cd /d "%~dp0"
if not defined SCRAM_DEMO_INPUT set "SCRAM_DEMO_INPUT=%~dp0artifacts\optimization\candidates\qwen05b-observer-20261118-demo01\demo-inputs.json"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "TOKENIZERS_PARALLELISM=false"
"%~dp0.venv-train\Scripts\python.exe" -u -m training.run_demo_observer --evidence-review --inputs "%SCRAM_DEMO_INPUT%"
exit /b %errorlevel%
