# P5 handover — Bosch chaos engineering

## Readiness and requirement coverage

P1–P4 have integration fixes on this branch. P5 and final acceptance remain. Tests reduce risk; they cannot guarantee freedom from future crashes.

| Hackathon requirement | Implemented | Remaining |
|---|---|---|
| Open-source app and map | Two Podinfo containers, manually maintained map, verification script | Automatic discovery absent |
| Intelligent planning | Deterministic templates, verified target, fresh baseline, stable ranking | Fixed heuristic, not adaptive/learned AI; confirm this meets organizers' AI expectation |
| Controlled failures | Chaos Toolkit/Toxiproxy, allowlists, caps, locks, independent watchdog, kill switch | Keep one active experiment in this sandbox |
| Observe behavior | P4 windows: latency, errors, health, throughput; P3 recovery probes | P5 charts/timeline |
| Analyze/report | Deterministic flags, recovery assessment, evidence | Dashboard, recommendations and defined resilience score |

Sarvam/LLMs are not integrated; no LLM/agent evals exist. Rules are deterministic, live timings vary. Kubernetes is not required.

Measured route: POST http://127.0.0.1:9898/echo → frontend → Toxiproxy → backend. GET /healthz alone does not test the dependency. Management API: localhost:8474; proxy data port 8666/backend 9898 stay inside Docker.

## Setup from repository root — PowerShell

Prerequisites: Git, Python 3.12, Docker Desktop with Linux containers. Shared environment:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r chaos-execution/requirements.txt
.\.venv\Scripts\python.exe -m pip install -e './p4-metrics[dev]'
$repo = (Get-Location).Path
& ./chaos-execution/sandbox/start.ps1
Set-Location $repo
& ./chaos-execution/sandbox/verify.ps1
Set-Location $repo
Invoke-RestMethod http://127.0.0.1:8474/proxies/podinfo-backend
```

Scripts change directory; restore root as shown. Verification must pass; proxy must be enabled with zero toxics. Never run campaigns/demos alongside the integrated flow. Environment overrides CHAOS_RUNS_DIR/CHAOS_METRICS_PATH must be identical absolute paths in every process.

## Integrated run — three terminals at repository root

Terminal A: leave the independent watchdog running.

```powershell
$env:PYTHONPATH = (Resolve-Path ./chaos-execution).Path
.\.venv\Scripts\python.exe -m chaos.watchdog
```

Terminal B: generate plan and baseline.

```powershell
.\.venv\Scripts\python.exe -m planner.planner
if ($LASTEXITCODE -ne 0) { throw 'Planner blocked; do not continue' }
$runId = (Get-Content ./planner/experiment-plan.json -Raw | ConvertFrom-Json).run_id
.\.venv\Scripts\python.exe -m p4_metrics.cli traffic --service-map chaos-execution/sandbox/service-map.json --experiment-plan planner/experiment-plan.json --phase baseline --duration 5 --output chaos-execution/runs/metrics.jsonl
```

P2 collects 30 planning requests. P3 separately requires 20 measured baseline requests for this run, recent data (120 seconds maximum age) and acceptable errors. Five seconds is a starting duration, not a guarantee of sufficient completions. Generate a new plan/baseline for each attempt; do not reuse blocked/finished run IDs.

Terminal C: arm sampler before runner.

```powershell
$runId = (Get-Content ./planner/experiment-plan.json -Raw | ConvertFrom-Json).run_id
.\.venv\Scripts\python.exe -m p4_metrics.cli traffic --service-map chaos-execution/sandbox/service-map.json --experiment-plan planner/experiment-plan.json --phase fault --output chaos-execution/runs/metrics.jsonl --p3-run-state "chaos-execution/runs/$runId/run.json"
```

Sampler waits up to 120 seconds for injection and exits on terminal state/result. Start runner promptly while baseline is fresh.

Terminal B: execute same plan.

```powershell
$env:PYTHONPATH = (Resolve-Path ./chaos-execution).Path
.\.venv\Scripts\python.exe -m chaos.runner planner/experiment-plan.json
Get-Content "chaos-execution/runs/$runId/result.json"
```

Inspect JSON: process exit alone is not a verdict. Continue only after verified cleanup, clean proxy, and sampler exit. If blocked, inspect block_reasons and generate a new plan. Stop the pipeline on cleanup failure.

Terminal B: recovery and evaluation.

```powershell
.\.venv\Scripts\python.exe -m p4_metrics.cli traffic --service-map chaos-execution/sandbox/service-map.json --experiment-plan planner/experiment-plan.json --phase recovery --duration 5 --output chaos-execution/runs/metrics.jsonl
.\.venv\Scripts\python.exe -m p4_metrics.cli evaluate --experiment-plan planner/experiment-plan.json --metrics-file chaos-execution/runs/metrics.jsonl --p3-result "chaos-execution/runs/$runId/result.json" --output "chaos-execution/runs/$runId/p4-result.json"
```

Do not regenerate plan between phases. Preserve its copy with run evidence before planning again. P5 must serialize the complete workflow and use immutable plans per job.

## P5 artifact contracts

| Artifact | Use |
|---|---|
| chaos-execution/sandbox/service-map.json | Dependency diagram; match plan target names |
| planner/ranked-candidates.json and experiment-plan.json | Ranking, chosen fault, intensity, duration, thresholds, reasons |
| chaos-execution/runs/RUN_ID/run.json | Transient state and boundaries |
| chaos-execution/runs/RUN_ID/result.json | Authoritative P3 status, cleanup, stop reasons, timeline, evidence |
| chaos-execution/runs/metrics.jsonl | P4 windows filtered by exact run_id/phase |
| chaos-execution/runs/RUN_ID/p4-result.json | Evaluation validity, flags and recovery |
| chaos-execution/runs/events.jsonl and results.jsonl | Append-only feeds; deduplicate event_id and reconcile per-run result |

P4 is one row per window, not request. latency.values contains durations but does not pair each with its HTTP status. Campaign per-request exports use a different contract.

P3 completed means lifecycle completion, aborted means early stop, blocked means rejected, failed needs investigation. P4 valid means evidence accepted, not resilience passed. Show cleanup, recovery and flags separately. Missing evidence is unknown/incomplete, never green. Short safety-stopped runs may have insufficient fault samples.

## P5 implementation and acceptance

1. One server-side queue, immutable plan/unique run ID per job, authoritative P3 validation. Coordinate baseline → sampler ready → runner → cleanup verified → sampler drained → recovery → evaluation.
2. Watchdog process stays independent of browser/dashboard. Handle blocked results without waiting forever for injection. Never auto-start another run after cleanup failure.
3. Show map, ranking/reasons, phase, latency/error/throughput charts, timeline, stop reason, cleanup and recovery.
4. Manual stop creates runs/KILL or runs/RUN_ID/STOP; watchdog cleans up. Dashboard exit/refresh must not kill watchdog.
5. Reports link evidence and use explicit rule-to-recommendation mappings. Define score formula and missing-data behavior before claiming a resilience score.
6. Slack uses a separate worker, bounded timeouts/retries, event-ID deduplication. Keep credentials outside Git. Delivery failure must never block rollback.
7. Retry a partially appended final JSONL line on the next poll; surface malformed completed records. Serve only allowlisted run artifacts.

Acceptance: normal completion, threshold abort, runner crash, manual stop, browser refresh, missing metrics, invalid plan, unavailable Docker/Toxiproxy and failed Slack delivery. P3 demo metrics are a stand-in, not a substitute for the integrated P4 test.

## Safety and known limits

P3 supports latency, timeout, reset_peer, limit_data, bandwidth and connection-disabled; P2 templates cover a subset. This flow does not implement CPU, disk, database, container crash or traffic-spike experiments. Runner-crash demo kills the controller, not the app container.

From root, request stop with `New-Item -ItemType File -Force chaos-execution/runs/KILL`. Keep watchdog alive until cleanup verification. Inspect proxy enabled/toxics, result/watchdog JSON and real echo route. Never clear locks/flags while a runner is active. Follow P3 reset instructions after stopping runners/checking cleanup; clear kill only when deliberately ready for another run. After cleanup shut down with `docker compose -f chaos-execution/sandbox/docker-compose.yml down`.

Docker/host failure may prevent immediate cleanup. Recovery probes include HTTP timeout overhead. Requests can span fault removal; P4 boundary windows are approximate. P4 recovery compares thresholds, not sustained recovery time. Planner currently handles one verified route. Dependencies use ranges, not a lockfile; freeze the validated environment before presentation.

## Tests

Run separately because P3 has its own test import path:

```powershell
.\.venv\Scripts\python.exe -m pytest planner/tests -q
Push-Location chaos-execution
..\.venv\Scripts\python.exe -m pytest tests -q
Pop-Location
Push-Location p4-metrics
..\.venv\Scripts\python.exe -m pytest tests -q
Pop-Location
```

Then perform a real integrated latency run: same run ID throughout, observed impact, verified cleanup and measured recovery. Component details: [P2](planner/README.md), [P3](chaos-execution/README.md), [P4](p4-metrics/README.md).

## Review evidence — 10 October 2026

- P2: 24 tests passed. P3: 62 tests passed. P4: 23 tests passed (109 total).
- Live Docker smoke run: run-20261010T103053669675Z, 100 ms latency plan for 5 seconds. P3 completed with cleanup_verified=true; P4 valid/recovery success; proxy clean afterward. Observed flag: LATENCY_CRITICAL_SPIKE.
- This smoke used a watchdog thread in the test harness. It verifies the normal live integration path, not independent-process crash survival. P3 automated watchdog tests cover crash/kill/threshold behavior against a fake proxy; repeat the independent-process demos before final presentation.
- Fixed stale plan reuse after planner failure, reused P3 run IDs, pre-injection fault sampling, zero-concurrency hangs, malformed-metrics interference with cleanup, false recovery success with missing evidence/unhealthy recovery, and multi-run metrics feed rejection. Evidence references now reach P4 output.
- Review scope: planner, runner/watchdog handoffs, measurement/evaluation and runbook. This is not a formal security audit or proof that every possible failure is handled.
