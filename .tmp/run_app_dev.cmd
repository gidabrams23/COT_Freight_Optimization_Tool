@echo off
set APP_ENV=development
set FLASK_ENV=development
set FLASK_SECRET_KEY=dev-session-key
cd /d "c:\Users\gabramowitz\OneDrive - Council Advisors\Documents\2. Firm Involvement\Second Project\AppDev-V2"
venv\Scripts\python.exe app.py 1>> app_stdout.log 2>> app_stderr.log
