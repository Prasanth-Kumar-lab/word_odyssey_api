@echo off
cd /d "%~dp0"
set MONGO_URI=mongodb://localhost:27017/
set MONGODB_DB=word_odyssey
"C:\Users\bunny\AppData\Local\Programs\Python\Python312\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
