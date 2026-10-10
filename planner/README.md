# P2 experiment planner

P2 reads the verified P1 service map, gathers a fresh 30-request baseline through its mapped caller route, ranks approved fault candidates deterministically, and writes one plan for P3.

From the repository root, with the P1 sandbox running:

```powershell
python -m planner.planner
```

The default map is `chaos-execution/sandbox/service-map.json`; baseline requests use its single verified dependency route (currently `POST http://localhost:9898/echo`). The live baseline is saved to `planner/live-baseline.json` and the plan to `planner/experiment-plan.json`. Planning stops if the map is unverified, the baseline is unhealthy/stale, or no approved candidate targets a mapped dependency.

For offline development, pass `--baseline planner/fixtures/baseline.json`; the planner still rejects it as stale for an actual plan. Tests use the fixture directly to verify deterministic ranking without injecting faults.

P2 emits the canonical P3 fields (`run_id`, `candidate_id`, `target`, `fault_type`, numeric `intensity`, `max_duration`, `stop_thresholds`, `reason_codes`) and includes the verified `request_path` for P4 when there is one caller route.

```powershell
python -m pytest planner/tests -q
```
