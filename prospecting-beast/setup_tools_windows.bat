@echo off
python -m pip install -r tools\requirements_tools.txt
if %errorlevel% neq 0 pause
