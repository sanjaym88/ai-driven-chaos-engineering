"""Simulate a LIVE experiment run, so you can test the dashboard + notifier end to end
without P1-P4. It writes files exactly like the real team will:

  metrics.jsonl  - one line per second while the "experiment" runs (baseline -> fault -> recovery)
  plan.json      - at the start
  result.json    - only at the END (that is what triggers the Slack notifier)

Usage (from the p5-dashboard folder):
  python tools/simulate_run.py                      # completed run, 800 ms delay
  python tools/simulate_run.py --outcome aborted    # watchdog stops it on p95 breach
  python tools/simulate_run.py --outcome failed     # errors, cleanup not verified
  python tools/simulate_run.py --fast               # no waiting (for quick checks)

Open the dashboard with "Auto-refresh every 5 s" ON and watch the new run appear and grow.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from p5 import config  # noqa: E402


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outcome", choices=["completed", "aborted", "failed"], default="completed")
    ap.add_argument("--latency", type=int, default=800, help="injected delay in ms")
    ap.add_argument("--fast", action="store_true", help="don't sleep between lines")
    a = ap.parse_args()

    runs = config.runs_dir()
    runs.mkdir(parents=True, exist_ok=True)
    rid = f"sim-{datetime.now():%H%M%S}"
    d = runs / rid
    d.mkdir()
    latency = 2500 if a.outcome == "aborted" else a.latency
    (d / "plan.json").write_text(json.dumps({
        "run_id": rid, "candidate_id": f"sim-lat-{latency}", "target": "frontend->backend",
        "fault_type": "reset_peer" if a.outcome == "failed" else "latency",
        "intensity": {"timeout_ms": 0} if a.outcome == "failed" else {"latency_ms": latency},
        "max_duration_s": 20, "stop_thresholds": {"p95_ms": 2000, "error_rate": 0.2},
        "reason_codes": ["R_EDGE_ON_USER_PATH", "R_SIMULATED"], "score": 0.8, "rank": 1,
        "planner": "simulator"}, indent=2))
    print(f"Simulating {a.outcome} run '{rid}' in {d}")

    started = now_iso()
    fault_start = fault_end = None
    stop_reason, stopped_by = "duration_expired", None

    def emit(phase, extra=0.0, err=0.0):
        p50 = 12 + extra + random.uniform(-2, 2)
        line = {"run_id": rid, "timestamp": now_iso(), "phase": phase, "request_count": 10,
                "errors": sum(random.random() < err for _ in range(10)),
                "latency": {"p50_ms": round(p50, 1), "p95_ms": round(p50 * 1.3 + random.uniform(0, 30), 1),
                            "max_ms": round(p50 * 1.6, 1)},
                "health": {"frontend": "up", "backend": "up"}}
        with open(d / "metrics.jsonl", "a") as fh:
            fh.write(json.dumps(line) + "\n")
        if not a.fast:
            time.sleep(1)
        return line

    for _ in range(10):
        emit("baseline")
    fault_start = now_iso()
    print("  fault injected")
    for i in range(20):
        if a.outcome == "failed":
            line = emit("fault", err=0.6)
        else:
            line = emit("fault", extra=latency)
        if a.outcome == "aborted" and line["latency"]["p95_ms"] > 2000 and i >= 2:
            stop_reason = f"threshold_breach: p95 {line['latency']['p95_ms']:.0f} ms > 2000 ms"
            stopped_by = "watchdog"
            print(f"  watchdog: {stop_reason}")
            break
    fault_end = now_iso()
    print("  fault removed, verifying recovery")
    for i in range(10):
        if a.outcome == "failed":
            emit("recovery", err=0.5)
        else:
            emit("recovery", extra=300 if i < 2 else 0)

    status = a.outcome
    result = {"run_id": rid, "status": status, "started_at": started, "ended_at": now_iso(),
              "fault_started_at": fault_start, "fault_ended_at": fault_end,
              "stop_reason": stop_reason, "stopped_by": stopped_by,
              "cleanup_verified": status != "failed",
              "recovery_result": ({"status": "not_recovered", "recovery_time_s": None} if status == "failed"
                                  else {"status": "recovered", "recovery_time_s": 4.0}),
              "evidence_refs": [{"type": "metrics", "path": "metrics.jsonl"}]}
    if status == "failed":
        result["failure_detail"] = "simulated: toxic still present after 3 cleanup retries"
    # write atomically so the notifier never reads half a file
    tmp = d / "result.json.tmp"
    tmp.write_text(json.dumps(result, indent=2))
    tmp.replace(d / "result.json")
    print(f"  done -> {status}. result.json written (the notifier will pick it up).")


if __name__ == "__main__":
    main()
