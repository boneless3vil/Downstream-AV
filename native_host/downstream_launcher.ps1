# Native messaging host for the Downstream browser extension.
#
# The browser runs this (via downstream_launcher.bat) when the extension finds
# the desktop app's API down. It starts Downstream.exe if it isn't already
# running, replies with one native-messaging message and exits.
#
# Why PowerShell and not plain `start` in the .bat: the browser talks to a
# native host over stdin/stdout pipes, and cmd's `start` hands copies of
# those pipe handles to the launched app. The app then holds the browser's
# stdout pipe open for its whole lifetime, so the browser never sees the host
# finish and the extension's sendNativeMessage() call never returns.
# Start-Process launches via ShellExecute, which does not pass our handles
# on, so the pipe closes as soon as this script exits.

$ErrorActionPreference = 'Stop'

# Deployed layout: <app dir>\native_host\<this script>, exe one level up.
# Dev layout: <repo>\native_host\<this script>, exe in <repo>\dist.
$exe = Join-Path (Split-Path $PSScriptRoot -Parent) 'Downstream.exe'
if (-not (Test-Path $exe)) {
    $exe = Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\Downstream.exe'
}

$reply = @{ launched = $false; running = $false; error = $null }
try {
    if (Get-Process -Name 'Downstream' -ErrorAction SilentlyContinue) {
        $reply.running = $true
    } elseif (Test-Path $exe) {
        Start-Process -FilePath $exe -WorkingDirectory (Split-Path $exe -Parent)
        $reply.launched = $true
    } else {
        $reply.error = "Downstream.exe not found next to $PSScriptRoot"
    }
} catch {
    $reply.error = $_.Exception.Message
}

# Native messaging wire format: 4-byte little-endian length, then UTF-8 JSON
$json = ($reply | ConvertTo-Json -Compress)
$bytes = [Text.Encoding]::UTF8.GetBytes($json)
$out = [Console]::OpenStandardOutput()
$out.Write([BitConverter]::GetBytes([int32]$bytes.Length), 0, 4)
$out.Write($bytes, 0, $bytes.Length)
$out.Flush()
