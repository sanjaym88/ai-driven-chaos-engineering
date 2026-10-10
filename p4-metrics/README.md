# P4 metrics and deterministic evaluation

P4 sends bounded traffic to the caller route for a planned dependency, records timestamped one-second metric windows as JSONL, and evaluates baseline, fault, and recovery data together with P3's run result. Each JSONL object represents one observation window; `latency.values` contains the individual request durations observed in that window. These are not one-JSON-object-per-request records.

Files under `examples/` and the root `metrics.jsonl` / `result.json` are clearly illustrative fixtures; they are not live experiment results.

## Install

Python 3.10 or newer is required.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
p4-metrics --help
```

## P1 route mapping

P4 accepts the verified P1 service map where `services` and `dependencies` are arrays. Given a P3 plan with target `podinfo-backend`, it finds the mapped caller and sends the edge's request through that caller. For this sandbox that means `POST http://localhost:9898/echo`; the separate health probe is `GET http://localhost:9898/healthz`. POST requests use `request_body` from the plan, or a small JSON probe body if omitted.

The `examples/service-map.example.json` file mirrors that P1 map contract. For a real run, pass P1's verified `service-map.json` explicitly; don't assume a fixture is verified just because it parses.

## P2 → P3 → P4 run

The P3 watchdog reads `chaos-execution/runs/metrics.jsonl`. P4 must append every phase to that same file so P3 can verify a fresh baseline and observe live thresholds. P4 also writes its safety-trip flag beside that file, where P3's watchdog checks it. Use one unique `run_id` and only one active experiment per Toxiproxy target.

From the repository root, after starting the P1 sandbox and installing P3/P4 dependencies:

1. Start P3's independent watchdog in its own terminal from `chaos-execution`:
   `python -m chaos.watchdog`
2. Generate P2's plan using the verified sandbox map and a live 30-request baseline:
   `python -m planner.planner`
   This writes `planner/experiment-plan.json`. P2 fails closed if the baseline is unhealthy or older than five minutes.
3. Capture a fresh P4 baseline immediately before P3 preflight (at 10 requests/second, 5 seconds exceeds P3's 20-request minimum):
   `p4-metrics traffic --service-map chaos-execution/sandbox/service-map.json --experiment-plan planner/experiment-plan.json --phase baseline --duration 5 --output chaos-execution/runs/metrics.jsonl`
4. In one terminal, start P4's fault sampler; it exits when P3 writes a terminal `run.json` state:
   `p4-metrics traffic --service-map chaos-execution/sandbox/service-map.json --experiment-plan planner/experiment-plan.json --phase fault --output chaos-execution/runs/metrics.jsonl --p3-run-state chaos-execution/runs/<run_id>/run.json`
5. In a second terminal, start P3 with that exact plan:
   `cd chaos-execution; .\\scripts\\run.ps1 ..\\planner\\experiment-plan.json`
   P3's watchdog handles deadline, P4 safety trips, configured thresholds, and manual stop, then removes the toxic and verifies recovery.
6. After P3 has written `chaos-execution/runs/<run_id>/result.json`, collect recovery data:
   `p4-metrics traffic --service-map chaos-execution/sandbox/service-map.json --experiment-plan planner/experiment-plan.json --phase recovery --duration 5 --output chaos-execution/runs/metrics.jsonl`
7. Evaluate the complete run:
   `p4-metrics evaluate --experiment-plan planner/experiment-plan.json --metrics-file chaos-execution/runs/metrics.jsonl --p3-result chaos-execution/runs/<run_id>/result.json --output chaos-execution/runs/<run_id>/p4-result.json`

P3's campaign command runs its own traffic generator and should not run alongside P4 traffic for the same experiment. P5 should coordinate one sampler, start fault sampling with P3, wait for P3 cleanup before recovery sampling, and then invoke evaluation.

The P3 result must have the same `run_id`, status `completed` or `aborted`, a non-empty `stop_reason`, and `cleanup_verified: true`. Missing/mismatched metrics, missing phases, stale baseline, failed/blocked P3 status, or unverified cleanup produce an invalid evaluation.

## Safety signal

When a configured latency-p95 or error-rate stop threshold is crossed, P4 writes `safety-trip-<run_id>.flag` beside the shared metrics file. P3's independent watchdog reads that path and stops/cleans the fault. P4 is a signal source; P3 remains responsible for cleanup verification.

Thresholds are fractions for `max_error_rate` (for example `0.05` = 5%). The canonical latency key is `max_latency_p95_ms`; `max_p95_latency_ms` and `max_latency_ms` are accepted for compatibility. Latency is measured in milliseconds.

## Tests

```powershell
python -m pytest -q
```

Tests cover P1 route resolution, both safety thresholds, run-ID consistency, percentile severity classification, and missing or failed P3 evidence. The P4 evaluator is deterministic; live timings and application outcomes are real observations and can vary from run to run.
