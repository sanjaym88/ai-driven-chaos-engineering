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

## Run phases

P5 (or a local operator during manual testing) coordinates the phase order. P4 does not launch P3 itself:

1. Run `p4-metrics traffic --service-map <P1-map.json> --experiment-plan <plan.json> --phase baseline --duration 10 --output runs/<run_id>/metrics.jsonl`.
2. Start P4's `--phase fault` sampler and P3's experiment at the same time. The P4 fault sampler runs for the plan's `max_duration`; P3 must stop/rollback on duration, threshold, or manual stop. If P3 stops early, P5 stops the fault sampler and starts the recovery sampler after cleanup.
3. Run `p4-metrics traffic ... --phase recovery --duration 10 --output runs/<run_id>/metrics.jsonl` only after P3 confirms cleanup.
4. Evaluate after all three phases and P3's terminal result are available:

```powershell
p4-metrics evaluate --experiment-plan <plan.json> --metrics-file runs/<run_id>/metrics.jsonl --p3-result runs/<run_id>/p3-result.json --output runs/<run_id>/result.json
```

The P3 result is required for evaluation. It must contain the same `run_id`, a `status` of `completed`, `aborted`, `failed`, or `blocked`, a non-empty `stop_reason`, and boolean `cleanup_verified`. Missing/mismatched metrics, missing phases, stale baseline, or unverified cleanup fail closed. `--phase fault` is sampled concurrently with injection; do not run multiple experiments against the same proxy at once.

## Safety signal

When a configured latency-p95 or error-rate stop threshold is crossed, P4 writes `safety-trip-<run_id>.flag` in its current working directory. P3's independent watchdog must monitor that exact shared path and stop/clean the fault. This flag is a signal to P3; P4 is not the watchdog and cannot guarantee cleanup by itself. Use a unique run ID and shared working directory for P3/P4, or adapt both sides to a shared explicit signal path before running them in separate containers.

Thresholds are fractions for `max_error_rate` (for example `0.05` = 5%). The canonical latency key is `max_latency_p95_ms`; `max_p95_latency_ms` and `max_latency_ms` are accepted for compatibility. Latency is measured in milliseconds.

## Tests

```powershell
python -m pytest -q
```

Tests cover P1 route resolution, both safety thresholds, run-ID consistency, percentile severity classification, and missing or failed P3 evidence. The P4 evaluator is deterministic; live timings and application outcomes are real observations and can vary from run to run.
