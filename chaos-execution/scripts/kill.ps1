# Manual kill switch. The watchdog sees the flag within ~1s, removes every chaos-* fault and verifies cleanup.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
New-Item -ItemType Directory -Force "$root\runs" | Out-Null
Set-Content -Path "$root\runs\KILL" -Value "manual stop $(Get-Date -Format o)"
Write-Host "Kill flag set. Remove it afterwards with scripts\reset.ps1"
