@echo off
rem Native messaging host for the Downstream browser extension.
rem The browser runs this when the extension finds the desktop app's API
rem down; it starts the app (if needed) and exits without speaking the
rem native-messaging protocol - the extension polls the API instead of
rem reading a reply.
tasklist /FI "IMAGENAME eq Downstream.exe" 2>nul | find /I "Downstream.exe" >nul
if errorlevel 1 start "" "C:\bin\Downstream\Downstream.exe"
