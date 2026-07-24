# Registers the Downstream launcher native-messaging host for Edge and
# Chrome (current user only; no admin needed). Re-run any time - it
# overwrites the existing registration.
#
# NOTE: allowed_origins in the .json must contain the extension's ID. For
# an unpacked extension the ID is derived from its folder path, so if the
# extension folder ever moves, update the .json and re-run this.
$manifest = 'C:\bin\Downstream\native_host\com.boneless3vil.downstream_launcher.json'
$hostName = 'com.boneless3vil.downstream_launcher'
foreach ($hive in @(
    "HKCU:\Software\Microsoft\Edge\NativeMessagingHosts\$hostName",
    "HKCU:\Software\Google\Chrome\NativeMessagingHosts\$hostName")) {
    New-Item -Path $hive -Force | Out-Null
    Set-ItemProperty -Path $hive -Name '(Default)' -Value $manifest
    Write-Host "registered: $hive"
}
