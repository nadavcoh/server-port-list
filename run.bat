@echo off
echo "Activating virtual environment and running the server..."
title server-port-list
call .venv\Scripts\activate.bat && python app.py %*
pause
