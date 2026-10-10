# Start the independent watchdog in its own process. Leave this window open during experiments.
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
& "$root\.venv\Scripts\python.exe" -m chaos.watchdog
