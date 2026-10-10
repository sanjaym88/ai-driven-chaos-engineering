# AI-driven chaos engineering

**P5: start with the [handover and execution runbook](P5-HANDOVER.md).** It contains shared-environment setup, exact commands, contracts, remaining hackathon work and acceptance checks. P1–P4 integration is implemented on this branch; P5 is not yet complete. Tests cannot guarantee freedom from future crashes.

This repository runs a bounded network-fault experiment against the Podinfo sandbox. P1 supplies a verified service map, P2 chooses one deterministic candidate from a live baseline, P3 validates and injects one Toxiproxy fault with an independent cleanup watchdog, and P4 records/evaluates the baseline, fault, and recovery phases. P5 can use P3's run/event JSONL and P4's metrics/evaluation JSON as dashboard inputs.

## Quick start (Windows / Docker Desktop)

1. Start and verify the sandbox:
   ```powershell
   cd chaos-execution\sandbox
   .\start.ps1
   .\verify.ps1
   cd ..\..
   ```
2. Install P3 and P4 dependencies in their package environments, following [P3 setup](chaos-execution/README.md) and [P4 setup](p4-metrics/README.md).
3. Start the independent P3 watchdog in a dedicated terminal from `chaos-execution`:
   `python -m chaos.watchdog`
4. From the repository root, create a candidate plan from live traffic:
   `python -m planner.planner`
5. Follow the shared metrics path and terminal-state commands in [P4's run guide](p4-metrics/README.md). Run one experiment at a time against Toxiproxy.

P2's unit tests, P3's lifecycle/watchdog tests, and P4's metric/evaluation tests can run without injecting a live fault. A real fault should run only in the local sandbox after verifying the watchdog is alive and the proxy has no active toxics.

## Shared run artifacts

- `planner/experiment-plan.json`: the selected, P3-compatible plan.
- `chaos-execution/runs/metrics.jsonl`: shared P4 metric windows consumed by the P3 watchdog and P4 evaluator.
- `chaos-execution/runs/<run_id>/result.json`: P3 stop reason, cleanup verification, recovery result, and evidence refs.
- `chaos-execution/runs/<run_id>/p4-result.json`: deterministic P4 evaluation for the dashboard.
- `chaos-execution/runs/events.jsonl` and `results.jsonl`: append-only P5 event/result feeds.

Examples and fixtures are illustrative; live experiments should use the verified sandbox map and fresh baseline.
