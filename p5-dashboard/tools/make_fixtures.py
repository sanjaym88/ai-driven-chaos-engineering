"""Generate the four sample runs (completed / aborted / failed / blocked) + a service map.

Deterministic (seeded): running it twice produces identical files, so tests can rely on them.
These are hand-designed MOCKS that follow contracts/CONTRACTS.md. Replace with real P3/P4
output at merge time (just point P5_RUNS_DIR at the team's runs folder).

    python tools/make_fixtures.py
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "fixtures"
STEP_S = 2  # one metrics line every 2 seconds
rng = random.Random(42)


def ts(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def line(run_id, t, phase, base_ms, extra_ms=0.0, err_rate=0.0, health=None, reqs=10):
    p50 = round(base_ms + extra_ms + rng.uniform(-2, 2), 1)
    p95 = round(p50 * 1.35 + rng.uniform(0, 4) if extra_ms == 0 else p50 + rng.uniform(15, 45), 1)
    errors = sum(rng.random() < err_rate for _ in range(reqs))
    return {"run_id": run_id, "timestamp": ts(t), "phase": phase, "request_count": reqs,
            "errors": errors, "latency": {"p50_ms": p50, "p95_ms": p95, "max_ms": round(p95 * 1.1, 1)},
            "health": health or {"frontend": "up", "backend": "up"}}


def write_run(run_id, plan, result, lines):
    d = OUT / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")
    (d / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    if lines is not None:
        (d / "metrics.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    (d / "journal.json").write_text(json.dumps({
        "_note": "MOCK Chaos Toolkit journal - real one comes from P3",
        "experiment": {"title": f"{plan['fault_type']} on {plan['target']}"},
        "status": result["status"], "start": result.get("started_at"), "end": result.get("ended_at"),
        "run": [], "rollbacks": []}, indent=2) + "\n")


def plan(run_id, cand, fault, intensity, dur, reasons, score, rank):
    return {"run_id": run_id, "candidate_id": cand, "target": "frontend->backend", "fault_type": fault,
            "intensity": intensity, "max_duration_s": dur,
            "stop_thresholds": {"p95_ms": 2000, "error_rate": 0.2},
            "reason_codes": reasons, "score": score, "rank": rank, "planner": "rule-baseline v1 (mock)"}


def main():
    (OUT / "runs").mkdir(parents=True, exist_ok=True)
    (OUT / "service-map.json").write_text(json.dumps({
        "version": "1",
        "services": [
            {"id": "client", "name": "Traffic generator (P4)", "port": None, "health_check": None},
            {"id": "frontend", "name": "Podinfo frontend", "port": 9898, "health_check": "/readyz"},
            {"id": "toxiproxy", "name": "Toxiproxy (fault point)", "port": 8474, "health_check": "/version"},
            {"id": "backend", "name": "Podinfo backend", "port": 9899, "health_check": "/readyz"}],
        "edges": [
            {"from": "client", "to": "frontend", "via": None, "verified": True},
            {"from": "frontend", "to": "backend", "via": "toxiproxy:backend", "verified": True}],
        "request_paths": [
            {"name": "home", "endpoint": "GET /", "path": ["client", "frontend"]},
            {"name": "echo", "endpoint": "POST /echo", "path": ["client", "frontend", "backend"]}],
        "notes": ["MOCK map from P5 fixtures. Redis not configured, so not claimed as a dependency.",
                  "Frontend /echo returns HTTP 200 even when the backend call fails; P4 counts those as errors."],
        "reviewed_by": "P1 (mock)", "source": "docker-compose.yml + manual review"}, indent=2) + "\n")

    t0 = datetime(2026, 10, 9, 9, 0, 0, tzinfo=timezone.utc)

    # ---- run-0001: completed - 800 ms delay for 30 s, recovers in ~4 s
    rid, t, L = "run-0001", t0, []
    for _ in range(15): L.append(line(rid, t, "baseline", 12)); t += timedelta(seconds=STEP_S)
    fault_start = t
    for _ in range(15): L.append(line(rid, t, "fault", 12, extra_ms=800)); t += timedelta(seconds=STEP_S)
    fault_end = t
    for i in range(10): L.append(line(rid, t, "recovery", 12, extra_ms=180 if i < 2 else 0)); t += timedelta(seconds=STEP_S)
    write_run(rid, plan(rid, "lat-fe-be-800", "latency", {"latency_ms": 800, "jitter_ms": 0}, 30,
                        ["R_EDGE_ON_USER_PATH", "R_SINGLE_INSTANCE", "R_NO_CLIENT_TIMEOUT"], 0.82, 1),
              {"run_id": rid, "status": "completed", "started_at": ts(t0), "ended_at": ts(t),
               "fault_started_at": ts(fault_start), "fault_ended_at": ts(fault_end),
               "stop_reason": "duration_expired", "cleanup_verified": True,
               "recovery_result": {"status": "recovered", "recovery_time_s": 4.0},
               "evidence_refs": [{"type": "journal", "path": "journal.json"},
                                 {"type": "metrics", "path": "metrics.jsonl"}]}, L)

    # ---- run-0002: aborted - 2500 ms delay trips the p95 threshold after ~6 s
    rid, start, t, L = "run-0002", t0 + timedelta(minutes=10), t0 + timedelta(minutes=10), []
    for _ in range(15): L.append(line(rid, t, "baseline", 12)); t += timedelta(seconds=STEP_S)
    fault_start = t
    for _ in range(3): L.append(line(rid, t, "fault", 12, extra_ms=2500)); t += timedelta(seconds=STEP_S)
    fault_end = t
    breach = L[-1]["latency"]["p95_ms"]
    for i in range(10): L.append(line(rid, t, "recovery", 12, extra_ms=400 if i < 3 else 0)); t += timedelta(seconds=STEP_S)
    write_run(rid, plan(rid, "lat-fe-be-2500", "latency", {"latency_ms": 2500, "jitter_ms": 0}, 30,
                        ["R_EDGE_ON_USER_PATH", "R_THRESHOLD_PROBE"], 0.74, 2),
              {"run_id": rid, "status": "aborted", "started_at": ts(start), "ended_at": ts(t),
               "fault_started_at": ts(fault_start), "fault_ended_at": ts(fault_end),
               "stop_reason": f"threshold_breach: p95 {breach:.0f} ms > 2000 ms",
               "stopped_by": "watchdog", "cleanup_verified": True,
               "recovery_result": {"status": "recovered", "recovery_time_s": 6.0},
               "evidence_refs": [{"type": "journal", "path": "journal.json"},
                                 {"type": "metrics", "path": "metrics.jsonl"},
                                 {"type": "watchdog_log", "path": "watchdog.log"}]}, L)

    # ---- run-0003: failed - connection reset; cleanup not verified, no recovery in window
    rid, start, t, L = "run-0003", t0 + timedelta(minutes=20), t0 + timedelta(minutes=20), []
    for _ in range(15): L.append(line(rid, t, "baseline", 12)); t += timedelta(seconds=STEP_S)
    fault_start = t
    for _ in range(10): L.append(line(rid, t, "fault", 12, err_rate=0.65)); t += timedelta(seconds=STEP_S)
    fault_end = t
    for _ in range(30): L.append(line(rid, t, "recovery", 12, err_rate=0.6)); t += timedelta(seconds=STEP_S)
    write_run(rid, plan(rid, "reset-fe-be", "reset_peer", {"timeout_ms": 0}, 20,
                        ["R_EDGE_ON_USER_PATH", "R_CONNECTION_FAULT"], 0.61, 3),
              {"run_id": rid, "status": "failed", "started_at": ts(start), "ended_at": ts(t),
               "fault_started_at": ts(fault_start), "fault_ended_at": ts(fault_end),
               "stop_reason": "duration_expired", "cleanup_verified": False,
               "failure_detail": "cleanup check: toxic 'reset_peer_downstream' still listed on proxy 'backend' after 3 retries",
               "recovery_result": {"status": "not_recovered", "recovery_time_s": None},
               "evidence_refs": [{"type": "journal", "path": "journal.json"},
                                 {"type": "metrics", "path": "metrics.jsonl"}]}, L)

    # ---- run-0004: blocked - stale baseline, nothing injected
    rid, start = "run-0004", t0 + timedelta(minutes=30)
    t, L = start - timedelta(seconds=100), []
    for _ in range(3): L.append(line(rid, t, "baseline", 12)); t += timedelta(seconds=STEP_S)
    write_run(rid, plan(rid, "lat-fe-be-800", "latency", {"latency_ms": 800, "jitter_ms": 0}, 30,
                        ["R_EDGE_ON_USER_PATH"], 0.82, 1),
              {"run_id": rid, "status": "blocked", "started_at": ts(start), "ended_at": ts(start),
               "fault_started_at": None, "fault_ended_at": None,
               "stop_reason": "blocked: baseline stale (last sample 94 s old > 10 s limit)",
               "cleanup_verified": None,
               "recovery_result": {"status": "not_applicable", "recovery_time_s": None},
               "evidence_refs": [{"type": "metrics", "path": "metrics.jsonl"}]}, L)
    print(f"fixtures written to {OUT}")


if __name__ == "__main__":
    main()
