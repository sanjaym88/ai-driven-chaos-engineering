# Run one experiment:  .\scripts\run.ps1 contracts\samples\experiment-plan.sample.json
param([Parameter(Mandatory=$true)][string]$Plan)
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root
& "$root\.venv\Scripts\python.exe" -m chaos.runner $Plan
