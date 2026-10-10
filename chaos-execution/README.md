# Chaos execution and safety (P3)

Bounded network-delay experiments on the Podinfo sandbox: validate the plan, inject through Toxiproxy with
Chaos Toolkit, stop automatically on a threshold breach, clean up, verify recovery, write `result.json`.

```
plan (P2) -> validator -> runner -> Chaos Toolkit -> Toxiproxy -> podinfo-backend
                              ^                          |
                 independent watchdog (own process) -----+   deadline / thresholds / kill flag / heartbeat
```

## Prerequisites
Docker Desktop (WSL2), Python 3.12, Git.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Tests (no Docker needed; a fake Toxiproxy and app are used)
```powershell
python -m pytest -q
```

## Quick demo (one terminal)
With Docker Desktop running and the sandbox started (step 1 below):
```powershell
python scripts\demo.py normal      # fault applied, completes, recovers
python scripts\demo.py threshold   # watchdog stops it early on a latency breach
python scripts\demo.py crash       # runner killed mid-run; watchdog still cleans up
python scripts\demo.py kill        # manual kill switch
```
It starts its own watchdog, records a baseline with `mock_metrics.py` (stand-in for P4), injects the fault,
prints live latency, and prints `result.json`. Each scenario takes under a minute.

## Fault campaign: 154 cases with per-request measurements
```powershell
python -m chaos.campaign --repeats 7 --rate 20     # 22 fault configs x 7 repeats = 154 cases, about 40 min
python -m chaos.campaign --id smoke --limit 3      # quick check (about 45 s)
python -m chaos.campaign --id c1 --resume          # continue an interrupted campaign
```
Fault types (`config/safety.yaml`): `latency` (1-2000 ms), `timeout` (ms), `reset_peer` (ms), `limit_data` (bytes),
`bandwidth` (KB/s), `connection-disabled`. Cases run **strictly one at a time**. Before and after every case the
campaign checks that Toxiproxy is clean (proxy enabled, **zero toxics of any kind**); if it can't restore that, it stops.

Per case: baseline traffic -> normal validated run (Chaos Toolkit + independent watchdog) -> recovery traffic.
Everything lands in `campaigns/<id>/`:

| File | Contents |
|---|---|
| `runs/<run_id>/requests.jsonl` | **every request**: `phase` (baseline / fault / recovery), `t_send`, `t_rel`, `latency_ms`, `status`, `ok`, `error` |
| `runs/<run_id>/summary.json` | per phase: `count`, `success_count`, `success_rate`, `p50/p95/p99/mean/max` latency, error breakdown; `recovery` block; Toxiproxy before/after check |
| `summary.csv` | one row per case x phase (count, success rate, p50/p95/p99, recovery time) |
| `cases.csv` | one row per case: status, stop reason, recovery time, Toxiproxy clean before / after |
| `by_config.csv`, `report.md` | pooled over repeats per (fault, intensity) |
| `all_requests.csv` | every request of every case in one table |
| `summary.json`, `summary.jsonl` | everything, machine-readable (`.jsonl` is written as cases finish) |

Definitions: latency percentiles use **successful requests only**; the phase of a request comes from the fault
timeline (`injected_at` / `removed_at`); **recovery time** = seconds from fault removal until the first of 5
consecutive successful requests within 1.5x the baseline p95. Hard-failure faults (timeout, reset, data cut,
disabled) trip the 50% error-rate safety limit and are stopped by the watchdog within about a second, so their
fault phase is short by design (`status: aborted`, `threshold_breach:error_rate`).

## Run for real, step by step
1. Start the P1 sandbox (see `sandbox/README-p1.md`):
   ```powershell
   cd sandbox; .\start.ps1; .\verify.ps1; cd ..
   ```
   (The `.ps1` scripts were patched to pass JSON to `curl.exe` through a temp file, because Windows PowerShell 5.1 strips quotes.)
   If a plain `powershell` window misbehaves, use `powershell -NoProfile -ExecutionPolicy Bypass -File .\start.ps1`.
2. Start the watchdog in its own window: `.\scripts\start-watchdog.ps1`
3. Baseline metrics (stand-in for P4): `python scripts\mock_metrics.py run-001 baseline 30`
4. Run the experiment: `.\scripts\run.ps1 contracts\samples\experiment-plan.sample.json`
   and, in another terminal while it runs, `python scripts\mock_metrics.py run-001 fault 20`.
5. Look at `runs\run-001\result.json`, `journal.json`, `watchdog.json` and `runs\events.jsonl`.

Manual stop: `.\scripts\kill.ps1`. Clean up after experiments: `.\scripts\reset.ps1`.

## Files that cross team boundaries
| File | From -> to | Where |
|---|---|---|
| `service-map.json` | P1 -> P2, P3 | `sandbox/service-map.json` (target = `toxiproxy.proxy_name`) |
| `experiment-plan.json` | P2 -> P3 | validated by `chaos/validator.py`, rules in `config/safety.yaml` |
| `metrics.jsonl` | P4 -> P3 | path in `config/safety.yaml`; baseline and fault rows carry the run ID |
| `result.json`, `events.jsonl`, `journal.json` | P3 -> P4, P5 | `runs/<run_id>/`, `runs/events.jsonl` |

### P4 sampler handoff
The configured `runs/metrics.jsonl` is the shared file P3 reads for fresh baseline checks and live watchdog thresholds. P4 must append its baseline, fault, and recovery phases to this path. Its safety-trip flag is written beside the metrics file and P3 checks it on every watchdog tick. During the fault phase, P4 accepts the run's `runs/<run_id>/run.json` path and stops sampling when P3 reaches `stopped` or `cleanup_failed`.

Use either P4's traffic sampler or the P3 campaign's built-in traffic generator for a run; do not run both against the same service at once. P3's `campaign` is still useful for standalone repeated fault matrices, while P5 should coordinate P4 as the sole sampler when consuming the shared P4 evaluation output.

## Output for P4 / P5 (what P3 writes)
| File | Contents |
|---|---|
| `runs/<run_id>/result.json` | `status` (completed / aborted / failed / blocked), `stop_reason`, `all_triggers` (every stop condition active at stop time; first = primary), `errors` (cleanup / toolkit errors), `cleanup_verified`, `recovery_result`, `plan` (snapshot), `timeline` (epoch seconds: `armed_at`, `injected_at`, `removed_at`, `finished_at`, `fault_duration_s`), `evidence_refs`, `written_by` |
| `runs/results.jsonl` | The same record, one line per finished run, appended. Easiest thing for P4 to tail. |
| `runs/events.jsonl` | Event queue (`completed`, `aborted`, `blocked`, `watchdog_stop`, `cleanup_failed`, `orphan_removed`) with unique `event_id`s for P5 / Slack |
| `runs/<run_id>/journal.json` | Chaos Toolkit journal |
| `runs/<run_id>/watchdog.json` | What the watchdog did (only if it acted) |

Use `timeline` to split `metrics.jsonl` into before / during / after windows. If the runner crashes, the watchdog writes
`result.json` itself (`written_by: "watchdog"`), so every run has one.

## Assumptions to confirm at M0
- `intensity` for `latency` is **milliseconds**; `max_duration` is **seconds**.
- `metrics.jsonl` rows: `timestamp` (epoch or ISO), `phase` (`baseline` / `fault` / `recovery`), `request_count`,
  `errors`, `latency` (a p95 in ms, or `{"p95": ms}`), `health`. Baseline rows must carry the same `run_id` as the plan.
- Stop thresholds: `error_rate` is a fraction (0.2 = 20%), `p95_latency_ms` is milliseconds.

## Safety behaviour
- Unknown target, unsupported fault, intensity/duration/threshold over the cap -> **blocked**, nothing injected.
- Missing or stale baseline, watchdog not running, target already locked -> **blocked**.
- Every fault is named `chaos-<run_id>`; cleanup only touches that run's fault, and orphan `chaos-*` toxics are swept.
- Watchdog triggers: kill flag, max duration, threshold breach, runner heartbeat lost. It cleans up even if the runner died.
- Safety stops still go through cleanup, recovery verification and a result (`aborted`). Unverified cleanup -> `failed` + `cleanup_failed` event.
- Slack/reporting only read `events.jsonl`; they cannot block cleanup.
