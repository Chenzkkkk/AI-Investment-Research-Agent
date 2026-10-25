@echo off
cd /d "%~dp0"
call conda run --no-capture-output -n investment-agent python -m streamlit run app.py
pause
