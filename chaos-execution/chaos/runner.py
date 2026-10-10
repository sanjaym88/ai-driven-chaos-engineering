"""Experiment lifecycle:  validate -> preflight -> arm -> inject -> hold -> clean up -> verify recovery -> result.

Usage:  python -m chaos.runner contracts/samples/experiment-plan.sample.json
Any failed prerequisite gives a BLOCKED result and injects nothing. The watchdog (separate process)
must already be running.
"""
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from . import lock, probes
from .actions import remove_fault
from .config import ROOT, load_safety, load_service_map, now, read_json, run_dir, write_json
from .result import build_timeline, write_result
from .validator import preflight, validate_plan

EXPERIMENT = ROOT / "experiments" / "network-fault.json"


def _heartbeat(rd, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            write_json(rd / "heartbeat", {"t": now()})
        except OSError:        # never let one failed write kill the heartbeat
            pass
        stop.wait(1)


def _blocked(plan: dict, reasons: list) -> dict:
    run_id = plan.get("run_id") if isinstance(plan.get("run_id"), str) else "invalid-run"
    rid = run_id if run_id.replace("-", "").replace("_", "").isalnum() and len(run_id) <= 40 else "invalid-run"
    return write_result(rid, "blocked", "validation_or_preflight_failed", True, "not_applicable", [], reasons)


def run_plan(plan: dict) -> dict:
    safety, smap = load_safety(), load_service_map()
    verdict = validate_plan(plan, safety, smap)
    if verdict.ok:
        verdict = preflight(plan, safety)
    if not verdict.ok:
        return _blocked(plan, verdict.reasons)

    run_id, target = plan["run_id"], plan["target"]
    if not lock.acquire(target, run_id):          # re-check atomically: another run may have won the race
        return _blocked(plan, [f"target busy: held by run {lock.owner(target)}"])
    rd = run_dir(run_id)
    write_json(rd / "run.json", {"plan": plan, "state": "armed", "armed_at": now()})
    hb_stop = threading.Event()
    threading.Thread(target=_heartbeat, args=(rd, hb_stop), daemon=True).start()

    env = {**os.environ, "CHAOS_RUN_ID": run_id, "PYTHONPATH": str(ROOT)}
    errors = []
    try:
        proc = subprocess.run([sys.executable, "-m", "chaostoolkit", "run", str(EXPERIMENT),
                               "--journal-path", str(rd / "journal.json"), "--rollback-strategy", "always"],
                              cwd=ROOT, env=env, check=False,
                              stderr=subprocess.DEVNULL if os.environ.get("CHAOS_QUIET") else None)
        if proc.returncode != 0:
            errors.append(f"chaos toolkit exited with code {proc.returncode}")
    finally:
        hb_stop.set()
        try:
            cleaned = remove_fault(run_id)          # idempotent; the watchdog may already have done it
        except Exception as e:
            cleaned = False
            errors.append(f"cleanup error: {e}")
        wd = read_json(rd / "watchdog.json") or {}
        errors += wd.get("errors", [])
        recovery = probes.wait_for_recovery(safety) if cleaned else "not_recovered"
        finished = now()
        run = read_json(rd / "run.json") or {}
        reason = wd.get("stop_reason") or run.get("stop_reason") or "experiment_error"
        triggers = wd.get("all_triggers") or [reason]
        ok = cleaned and recovery == "recovered"
        status = "failed" if not ok else ("completed" if reason == "duration_expired" else "aborted")
        lock.release(target, run_id)
        refs = [str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
                for p in (rd / "journal.json", rd / "run.json", rd / "watchdog.json") if p.exists()]
        return write_result(run_id, status, reason, cleaned, recovery, refs,
                            extra={"plan": plan, "all_triggers": triggers, "errors": errors,
                                   "timeline": build_timeline(run, finished), "written_by": "runner"})


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m chaos.runner <plan.json>")
    plan_ = json.loads(Path(sys.argv[1]).read_text(encoding="utf8"))
    print(json.dumps(run_plan(plan_), indent=2))
