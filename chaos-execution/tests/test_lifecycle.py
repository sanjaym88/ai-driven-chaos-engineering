"""Full runner loop through Chaos Toolkit against the fake sandbox."""
import threading
import time

from chaos import lock, runner, watchdog
from chaos.config import now, read_json, run_dir, runs_dir
from chaos.toxiproxy import Toxiproxy, toxic_name
from conftest import PROXY, good_baseline, write_metrics
from chaos.config import load_safety


def start_watchdog():
    stop = threading.Event()
    t = threading.Thread(target=watchdog.run_loop, args=(stop,), daemon=True)
    t.start()
    for _ in range(50):
        if (runs_dir() / "watchdog.alive").exists():
            break
        time.sleep(0.1)
    return stop, t


def test_blocked_when_prerequisites_missing(env, plan):
    result = runner.run_plan(plan)
    assert result["status"] == "blocked" and "watchdog unavailable" in result["block_reasons"]
    assert not Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"]).chaos_toxics(PROXY)


def test_invalid_plan_blocked_before_injection(env, plan):
    plan["fault_type"] = "cpu-hog"
    assert runner.run_plan(plan)["status"] == "blocked"
    assert not env[0]["toxics"]


def test_completed_run_cleans_up_and_recovers(env, plan):
    state, tmp = env
    write_metrics(tmp, [good_baseline("t1")])
    stop, t = start_watchdog()
    try:
        plan["max_duration"] = 3
        result = runner.run_plan(plan)
    finally:
        stop.set()
        t.join(3)
    assert result["status"] == "completed", result
    assert result["stop_reason"] == "duration_expired"
    assert result["cleanup_verified"] and result["recovery_result"] == "recovered"
    assert state["toxics"] == []
    assert lock.owner(PROXY) is None
    assert (run_dir("t1") / "journal.json").exists()
    # P4/P5 data: plan snapshot, timeline boundaries, trigger list and an all-runs index line
    assert result["plan"]["intensity"] == 300 and result["all_triggers"] == ["duration_expired"]
    tl = result["timeline"]
    assert tl["armed_at"] <= tl["injected_at"] < tl["removed_at"] <= tl["finished_at"]
    assert 2.5 < tl["fault_duration_s"] < 5.5
    assert '"t1"' in (runs_dir() / "results.jsonl").read_text()


def test_threshold_breach_aborts_run_but_still_recovers(env, plan):
    state, tmp = env
    write_metrics(tmp, [good_baseline("t1")])
    stop, t = start_watchdog()

    def breach():
        time.sleep(2)
        write_metrics(tmp, [{"run_id": "t1", "timestamp": now(), "phase": "fault",
                             "request_count": 10, "errors": 9, "latency": {"p95": 100}, "health": "ok"}])
    threading.Thread(target=breach, daemon=True).start()
    try:
        plan["max_duration"] = 30
        result = runner.run_plan(plan)
    finally:
        stop.set()
        t.join(3)
    assert result["status"] == "aborted", result
    assert result["stop_reason"] == "threshold_breach:error_rate"
    assert result["cleanup_verified"] and state["toxics"] == []


def test_second_run_on_same_target_blocked(env, plan):
    state, tmp = env
    write_metrics(tmp, [good_baseline("t1")])
    (runs_dir() / "watchdog.alive").write_text(str(now()))
    assert lock.acquire(PROXY, "someone-else")
    result = runner.run_plan(plan)
    assert result["status"] == "blocked" and any("busy" in r for r in result["block_reasons"])
