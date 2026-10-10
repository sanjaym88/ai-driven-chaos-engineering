# P5 monochrome dashboard

Read-only local dashboard using Python standard library and plain HTML/CSS/JS. No build step, external fonts, CDNs or frontend packages. Black/white/gray presentation with status text rather than color coding.

From repository root:

```powershell
python dashboard/server.py
```

Open http://127.0.0.1:8085. Defaults read P3 `chaos-execution/runs`, P4 shared `runs/metrics.jsonl`, and the sandbox service map. A custom artifact set:

```powershell
python dashboard/server.py --runs PATH_TO_RUNS --metrics PATH_TO_METRICS_JSONL
```

Run selection, five-second refresh (can be paused), window p95 chart, pooled phase percentiles, service map, thresholds, recorded timeline, result inspection and JSON report export. P4 percentiles here include all recorded durations, including failures. These are not campaign success-only percentiles. Evidence file labels indicate presence; JSON results can be inspected below or downloaded.

No data is fabricated. Empty directories show an empty state. Missing latency/recovery remains unknown. Completed is a lifecycle outcome, not a resilience pass. This is a viewer, not an orchestrator: launch experiments using the root handover runbook. Run/kill controls, Slack delivery and a resilience score are not implemented here. It does not probe Docker or infer current app health from historical results.

The server binds only to loopback and exposes fixed routes. Do not expose it publicly. Only point it at trusted local run artifacts. Stop with Ctrl+C. Tests: `python -m pytest dashboard/tests -q`.
