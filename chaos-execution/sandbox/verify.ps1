$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Write-Host 'Checking frontend and backend health...'
docker compose exec -T frontend podcli check http localhost:9898/healthz
if ($LASTEXITCODE -ne 0) { throw 'Frontend health check failed.' }
docker compose exec -T backend podcli check http localhost:9898/healthz
if ($LASTEXITCODE -ne 0) { throw 'Backend health check failed.' }

Write-Host 'Checking Toxiproxy route...'
$proxiesJson = & curl.exe --silent --show-error --fail --max-time 5 'http://localhost:8474/proxies'
if ($LASTEXITCODE -ne 0) { throw 'Could not read the Toxiproxy proxy list.' }
$proxies = $proxiesJson | ConvertFrom-Json
$proxy = $proxies.'podinfo-backend'
if (-not $proxy) { throw 'Proxy podinfo-backend was not found.' }
if ($proxy.upstream -ne 'backend:9898') { throw "Unexpected upstream: $($proxy.upstream)" }
if (-not $proxy.enabled) { throw 'The podinfo-backend proxy exists but is disabled.' }

Write-Host 'Sending an echo request through frontend -> Toxiproxy -> backend...'
$payload = '{"p1_verification":"request passed through configured backend"}'
$payloadFile = Join-Path $env:TEMP 'echo-payload.json'
[System.IO.File]::WriteAllText($payloadFile, $payload)
$responseLines = & curl.exe --silent --show-error --fail --max-time 10 -H 'Content-Type: application/json' --data-binary "@$payloadFile" 'http://localhost:9898/echo'
$responseText = $responseLines -join ''
if ($LASTEXITCODE -ne 0) { throw 'Echo request failed; inspect frontend/backend/Toxiproxy logs.' }
if (-not $responseText.Contains('p1_verification')) { throw "Unexpected echo response: $responseText" }

Write-Host 'PASS: app health, proxy config, and frontend-to-backend echo verified.' -ForegroundColor Green
Write-Host 'Inspect logs with: docker compose logs --tail=50 frontend backend toxiproxy'
