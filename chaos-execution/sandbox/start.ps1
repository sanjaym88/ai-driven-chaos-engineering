$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

docker compose up -d

$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    & curl.exe --silent --show-error --fail --max-time 2 'http://localhost:8474/version' | Out-Null
    if ($LASTEXITCODE -eq 0) {
        $ready = $true
        break
    }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw 'Toxiproxy management API did not become ready. Check: docker compose logs toxiproxy' }

$proxiesJson = & curl.exe --silent --show-error --fail --max-time 3 'http://localhost:8474/proxies'
if ($LASTEXITCODE -ne 0) { throw 'Could not read Toxiproxy proxy list.' }
$proxies = $proxiesJson | ConvertFrom-Json
$proxyExists = $null -ne $proxies.'podinfo-backend'

if (-not $proxyExists) {
    $body = '{"name":"podinfo-backend","listen":"0.0.0.0:8666","upstream":"backend:9898","enabled":true}'
    # Body goes via a temp file: Windows PowerShell 5.1 strips double quotes from native-command arguments.
    $bodyFile = Join-Path $env:TEMP 'toxiproxy-create.json'
    [System.IO.File]::WriteAllText($bodyFile, $body)
    & curl.exe --silent --show-error --fail --max-time 5 -H 'Content-Type: application/json' --data-binary "@$bodyFile" 'http://localhost:8474/proxies' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the Toxiproxy listener.' }
}

Write-Host 'Services started. Wait for frontend health, then run .\verify.ps1.'
docker compose ps
