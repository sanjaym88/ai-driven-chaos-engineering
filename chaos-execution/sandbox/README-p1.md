# P1 Podinfo app and service map

This local sandbox uses two instances of the open-source Podinfo app. The frontend forwards `POST /echo` to a backend Podinfo instance through Toxiproxy. This gives the team a concrete dependency link to map and inject network faults into.

The service map is intentionally marked `pending-local-run` until the verification script passes on your machine.

## Prerequisite

- Docker Desktop installed and running, with Docker Compose v2 available.
- Internet access to pull the official container images.

## Start

From PowerShell in this folder:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\start.ps1
```

Wait until both Podinfo containers are healthy, then run:

```powershell
.\verify.ps1
```

The verification checks frontend health, backend health, the Toxiproxy upstream configuration, and a real echo request through the frontend-to-backend route.

## Endpoints and ports

| Component | Local / container endpoint | Purpose |
|---|---|---|
| Frontend Podinfo | `http://localhost:9898` | App under observation; health at `/healthz`; readiness at `/readyz` |
| Toxiproxy management API | `http://localhost:8474` | Create and inspect the proxy; bound to localhost on the host |
| Toxiproxy data listener | `toxiproxy:8666` inside Compose | Frontend's configured backend route |
| Backend Podinfo | `backend:9898` inside Compose | Echo upstream; health at `/healthz` |

## Request path

```text
Test client -> frontend Podinfo :9898 /echo
            -> Toxiproxy :8666
            -> backend Podinfo :9898 /echo
```

`service-map.json` is the machine-readable handoff to P2 and P3. Keep the dependency's `verification` field pending until `verify.ps1` passes. Once verified, update `verification_status` to `verified`, set the edge verification to `passed`, and record the date and test result in a commit.

## Troubleshooting

```powershell
docker compose ps
docker compose logs --tail=100 frontend backend toxiproxy
Invoke-RestMethod http://localhost:8474/proxies
```

Stop the sandbox with:

```powershell
.\stop.ps1
```

The app endpoints and proxy management API are intended for local development only. Do not expose the management API to a public network.
