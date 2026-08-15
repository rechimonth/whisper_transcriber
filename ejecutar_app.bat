@echo off
cd /d "C:\Users\wingz\.gemini\antigravity-ide\scratch\whisper_transcriber"
call venv\Scripts\activate.bat
start "" /B venv\Scripts\pythonw.exe main.py
