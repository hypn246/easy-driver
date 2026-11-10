@echo off
pip install pyinstaller
pyinstaller --noconsole --onefile --uac-admin --name easy_window easy_window.py
echo.
echo Done. Your file is dist\easy_window.exe
pause
