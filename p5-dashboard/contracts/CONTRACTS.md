# Shared data contracts (P5's working version)

These are the four files from the team plan, with the exact shapes P5 builds against.
**Bring this file to the merge (M0/M4)** and compare it with what P1–P4 actually write.
If a field name or unit differs, change only `p5/adapters.py` — nothing else in P5.

Machine-checkable versions: `contracts/*.schema.json` (run `python -m p5 validate <runs_dir>`).

## Folder layout

```
<runs_dir>/
  <run_id>/
    plan.json          # experiment-plan.json for this run (P2, validated by P3)
    result.json        # outcome (P3)
    metrics.jsonl      # one line per sampling interval (P4)
    journal.json       # Chaos Toolkit journal (P3) - linked as evidence, not parsed
service-map.json       # (P1) - path set separately
```

## Agreed values

| Thing | Values |
|---|---|
| `status` | `completed` · `aborted` · `failed` · `blocked` |
| `phase` | `baseline` · `fault` · `recovery` |
| latency | milliseconds (`_ms` suffix) |
| time | UTC ISO-8601, e.g. `2026-10-10T09:15:02Z` |
| durations | seconds (`_s` suffix) |

## 1. service-map.json (P1)

```json
{
  "version": "1",
  "services": [
    {"id": "frontend", "name": "Podinfo frontend", "port": 9898, "health_check": "/readyz"}
  ],
  "edges": [
    {"from": "frontend", "to": "backend", "via": "toxiproxy:backend", "verified": true}
  ],
  "request_paths": [
    {"name": "echo", "endpoint": "POST /echo", "path": ["client", "frontend", "backend"]}
  ],
  "notes": ["Redis not configured; not claimed as a dependency."],
  "reviewed_by": "P1", "source": "docker-compose.yml + manual review"
}
```

## 2. plan.json — experiment-plan.json (P2)

```json
{
  "run_id": "run-0001",
  "candidate_id": "lat-frontend-backend-800",
  "target": "frontend->backend",
  "fault_type": "latency",
  "intensity": {"latency_ms": 800},
  "max_duration_s": 30,
  "stop_thresholds": {"p95_ms": 2000, "error_rate": 0.2},
  "reason_codes": ["R_EDGE_ON_USER_PATH", "R_SINGLE_INSTANCE"],
  "score": 0.82, "rank": 1,
  "planner": "rule-baseline v1"
}
```

## 3. metrics.jsonl (P4) — one JSON object per line

```json
{"run_id": "run-0001", "timestamp": "2026-10-10T09:15:02Z", "phase": "baseline",
 "request_count": 10, "errors": 0,
 "latency": {"p50_ms": 12.1, "p95_ms": 19.4, "max_ms": 25.0},
 "health": {"frontend": "up", "backend": "up"}}
```
`latency` may also be a plain number (treated as p95 ms). `errors` counts failed requests
in the interval, **including HTTP 200 responses whose body says the backend call failed**.

## 4. result.json (P3)

```json
{
  "run_id": "run-0002",
  "status": "aborted",
  "started_at": "2026-10-10T09:20:00Z",
  "ended_at": "2026-10-10T09:21:10Z",
  "stop_reason": "threshold_breach: p95 2410 ms > 2000 ms",
  "cleanup_verified": true,
  "recovery_result": {"status": "recovered", "recovery_time_s": 6.0},
  "evidence_refs": [{"type": "journal", "path": "journal.json"},
                    {"type": "metrics", "path": "metrics.jsonl"}]
}
```
Optional fields P5 uses when present: `fault_started_at`, `fault_ended_at`, `stopped_by`
(`watchdog` · `manual` · `runner`), `failure_detail` (free text explaining a `failed` run).

`stop_reason` prefixes: `duration_expired`, `threshold_breach: …`, `manual_stop`,
`runner_heartbeat_lost`, `blocked: …`, `error: …`. `recovery_result.status`:
`recovered` · `not_recovered` · `not_applicable` · `unknown`.

## Slack events (P3 → P5, file drop, never blocks)

P3 does nothing extra: the P5 worker watches `<runs_dir>` and queues one event per
final `result.json`. Event ID = `<run_id>:<status>` → duplicates are dropped.
