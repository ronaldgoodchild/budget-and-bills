@echo off
cd /d "%~dp0"
echo Starting Budget & Bills app...
echo (If port 5000 is already in use, it'll automatically pick the next free one.)
python app.py
pause
