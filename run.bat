@echo off
echo "Activating virtual environment and running the server..."
call .venv\Scripts\activate.bat && python app.py
pause
