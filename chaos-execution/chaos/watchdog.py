"""Independent watchdog. Run it as its own process:  python -m chaos.watchdog

It scans runs/*/run.json, so it needs nothing from the runner and keeps working if the runner dies.
Stop triggers: manual kill, max duration, threshold breach, runner heartbeat lost.
On a trigger it removes the run's fault, verifies the proxy is clean, checks app recovery and
records the outcome. Slack/report problems can never reach this code path.
"""
import threading
import time

from . import probes
from .config import load_safety, now, read_json, runs_dir, write_json
from .result import emit_event
from .toxiproxy import PREFIX, Toxiproxy, toxic_name
from .validator import HEALTHY, normalize_thresholds

ACTIVE = ("injecting", "injected")
DEADLINE_GRACE_S = 1.5   # the runner normally removes the fault first; the watchdog is the backstop


def _latency(row):
    v = row.get("latency")
    return v.get("p95") if isinstance(v, dict) else v


def threshold_breaches(run_id: str, thresholds: dict, safety: dict, t: float) -> list:
    """Every stop threshold the recent fault-phase metrics currently breach (can be several)."""
    from .config import parse_ts
    from .validator import read_metrics
    window = safety["watchdog"]["threshold_window_s"]
    rows = []
    for r in read_metrics(safety["sandbox"]["metrics_path"], run_id, "fault"):
        try:
            if parse_ts(r["timestamp"]) >= t - window:
                rows.append(r)
        except (KeyError, ValueError):
            continue
    thresholds = normalize_thresholds(thresholds)
    found = []
    if thresholds.get("required_health") and any(
            r.get("health") is not None and r["health"] not in HEALTHY for r in rows):
        found.append("threshold_breach:health")
    reqs = sum(r.get("request_count", 0) for r in rows)
    if not reqs:
        return found
    if "error_rate" in thresholds and sum(r.get("errors", 0) for r in rows) / reqs > thresholds["error_rate"]:
        found.append("threshold_breach:error_rate")
    p95s = [p for p in map(_latency, rows) if p is not None]
    if "p95_latency_ms" in thresholds and p95s and max(p95s) > thresholds["p95_latency_ms"]:
        found.append("threshold_breach:p95_latency")
    return found


def stop_reasons(run: dict, rd, safety: dict, t: float) -> list:
    """All triggers firing right now, most important first. The first one is the primary stop reason."""
    reasons = []
    if (runs_dir() / "KILL").exists() or (rd / "STOP").exists():
        reasons.append("manual_kill")
    if t > run["deadline"] + DEADLINE_GRACE_S:
        reasons.append("duration_expired")
    hb = read_json(rd / "heartbeat", {}).get("t", run.get("started_at", t))
    if t - hb > safety["watchdog"]["runner_heartbeat_timeout_s"]:
        reasons.append("runner_heartbeat_lost")
    reasons += threshold_breaches(run["plan"]["run_id"], run["plan"]["stop_thresholds"], safety, t)
    return reasons


def cleanup(tp: Toxiproxy, target: str, name: str, safety: dict, fault_type: str = "latency") -> tuple:
    """(verified, errors). Errors from failed attempts are kept as evidence, not swallowed."""
    errors = []
    for _ in range(safety["watchdog"]["cleanup_retries"]):
        try:
            tp.remove_fault(target, fault_type, name)
            if tp.is_clean(target, name):
                return True, errors
            errors.append("fault still present after removal attempt")
        except Exception as e:
            errors.append(f"cleanup error: {e}")
        time.sleep(1)
    return False, errors


def handle_stop(tp: Toxiproxy, run: dict, rd, reasons: list, safety: dict) -> None:
    plan = run["plan"]
    run_id, reason = plan["run_id"], reasons[0]
    verified, errors = cleanup(tp, plan["target"], toxic_name(run_id), safety, plan["fault_type"])
    recovery = probes.wait_for_recovery(safety) if verified else "not_recovered"
    run["state"] = "stopped" if verified else "cleanup_failed"
    run["stop_reason"] = reason
    run["all_triggers"] = reasons
    if verified:
        run.setdefault("removed_at", now())
    write_json(rd / "run.json", run)
    write_json(rd / "watchdog.json", {"run_id": run_id, "stop_reason": reason, "all_triggers": reasons,
                                      "errors": errors, "cleanup_verified": verified,
                                      "recovery_result": recovery, "at": now()})
    emit_event("watchdog_stop", run_id, stop_reason=reason, all_triggers=reasons, cleanup_verified=verified)
    if not verified:
        emit_event("cleanup_failed", run_id, urgent=True)
    if "runner_heartbeat_lost" in reasons:
        # The runner is dead and can't write a result, so the watchdog records it for P4 / P5.
        from . import lock
        from .result import build_timeline, write_result
        if verified:
            lock.release(plan["target"], run_id)      # a dead runner can't release its own lock
        write_result(run_id, "aborted" if verified and recovery == "recovered" else "failed", reason,
                     verified, recovery, [f"runs/{run_id}/run.json", f"runs/{run_id}/watchdog.json"],
                     extra={"plan": plan, "all_triggers": reasons, "errors": errors,
                            "timeline": build_timeline(run, now()), "written_by": "watchdog"})


def sweep_orphans(tp: Toxiproxy, safety: dict, active: set, active_targets: set = frozenset()) -> None:
    """Remove chaos-* toxics that belong to no active run, and re-enable a proxy left disabled."""
    for target in safety["targets"]:
        try:
            if target not in active_targets and not tp.proxy(target).get("enabled", True):
                tp.set_enabled(target, True)
                emit_event("orphan_removed", target, target=target, what="proxy re-enabled")
            for name in tp.chaos_toxics(target):
                if name not in active:
                    tp.remove_toxic(target, name)
                    emit_event("orphan_removed", name[len(PREFIX):], target=target)
        except Exception:
            continue


def tick(tp: Toxiproxy, safety: dict) -> None:
    active, active_targets = set(), set()
    for run_json in runs_dir().glob("*/run.json"):
        run = read_json(run_json)
        if not run or run.get("state") not in ACTIVE:
            continue
        rd = run_json.parent
        active.add(toxic_name(run["plan"]["run_id"]))
        active_targets.add(run["plan"]["target"])
        reasons = stop_reasons(run, rd, safety, now())
        if reasons:
            handle_stop(tp, run, rd, reasons, safety)
            active.discard(toxic_name(run["plan"]["run_id"]))
    sweep_orphans(tp, safety, active, active_targets)


def run_loop(stop: threading.Event | None = None) -> None:
    safety = load_safety()
    tp = Toxiproxy(safety["sandbox"]["toxiproxy_url"])
    stop = stop or threading.Event()
    while not stop.is_set():
        (runs_dir() / "watchdog.alive").write_text(str(now()))
        try:
            tick(tp, safety)
        except Exception as e:  # keep watching no matter what
            print(f"watchdog tick error: {e}")
        stop.wait(safety["watchdog"]["poll_interval_s"])


if __name__ == "__main__":
    print("watchdog running (Ctrl+C to stop)")
    run_loop()
