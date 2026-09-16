@echo off
rem Native messaging host for the Downstream browser extension (registered
rem by register_host.ps1). Browsers can only launch an .exe or .bat as a host,
rem so this shim just hands off to downstream_launcher.ps1, which starts the
rem app without leaking the browser's stdio pipes into it and replies over the
rem native-messaging protocol. Full paths so a stray PATH entry can't shadow them.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0downstream_launcher.ps1"
