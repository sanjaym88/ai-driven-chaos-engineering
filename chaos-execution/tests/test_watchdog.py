"""Watchdog stop paths. Each test arms a fault by hand (no runner), as if the runner had died."""
import time

from chaos import watchdog
from chaos.config import load_safety, now, run_dir, runs_dir, read_json, write_json
from chaos.toxiproxy import Toxiproxy, toxic_name
from conftest import PROXY, write_metrics


def arm(plan, *, started=None, deadline_in=30, heartbeat=True):
    rid = plan["run_id"]
    rd = run_dir(rid)
    t = started or now()
    write_json(rd / "run.json", {"plan": plan, "state": "injected", "started_at": t, "deadline": t + deadline_in})
    if heartbeat:
        write_json(rd / "heartbeat", {"t": now()})
    tp = Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"])
    tp.add_latency(PROXY, toxic_name(rid), plan["intensity"])
    return tp, rd


def fault_row(rid, errors, reqs, p95):
    return {"run_id": rid, "timestamp": now(), "phase": "fault", "request_count": reqs,
            "errors": errors, "latency": {"p95": p95}, "health": "ok"}


def assert_cleaned(tp, rd, reason):
    assert tp.is_clean(PROXY, toxic_name(rd.name))
    wd = read_json(rd / "watchdog.json")
    assert wd["stop_reason"] == reason
    assert wd["cleanup_verified"] and wd["recovery_result"] == "recovered"
    assert read_json(rd / "run.json")["state"] == "stopped"


def test_healthy_run_is_left_alone(env, plan):
    tp, rd = arm(plan)
    watchdog.tick(tp, load_safety())
    assert not tp.is_clean(PROXY, toxic_name("t1"))
    assert read_json(rd / "run.json")["state"] == "injected"


def test_error_rate_breach_stops_and_cleans(env, plan):
    state, tmp = env
    tp, rd = arm(plan)
    write_metrics(tmp, [fault_row("t1", errors=8, reqs=10, p95=100)])
    watchdog.tick(tp, load_safety())
    assert_cleaned(tp, rd, "threshold_breach:error_rate")


def test_latency_breach_stops_and_cleans(env, plan):
    state, tmp = env
    tp, rd = arm(plan)
    write_metrics(tmp, [fault_row("t1", errors=0, reqs=10, p95=9000)])
    watchdog.tick(tp, load_safety())
    assert_cleaned(tp, rd, "threshold_breach:p95_latency")


def test_duration_expiry(env, plan):
    tp, rd = arm(plan, started=now() - 100, deadline_in=5)
    watchdog.tick(tp, load_safety())
    assert_cleaned(tp, rd, "duration_expired")


def test_manual_kill_flag(env, plan):
    tp, rd = arm(plan)
    (runs_dir() / "KILL").write_text("stop")
    watchdog.tick(tp, load_safety())
    assert_cleaned(tp, rd, "manual_kill")


def test_runner_heartbeat_lost(env, plan):
    """Runner killed mid-experiment: nobody refreshes the heartbeat, the watchdog cleans up alone."""
    tp, rd = arm(plan, heartbeat=False, started=now() - 60, deadline_in=300)
    watchdog.tick(tp, load_safety())
    assert_cleaned(tp, rd, "runner_heartbeat_lost")


def test_orphan_toxic_is_swept(env):
    tp = Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"])
    tp.add_latency(PROXY, "chaos-leftover", 100)
    watchdog.tick(tp, load_safety())
    assert tp.is_clean(PROXY)


def test_unrecoverable_cleanup_emits_failure(env, plan, monkeypatch):
    tp, rd = arm(plan, started=now() - 100, deadline_in=5)
    monkeypatch.setattr(tp, "remove_toxic", lambda *a: (_ for _ in ()).throw(RuntimeError("proxy down")))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    watchdog.tick(tp, load_safety())
    assert read_json(rd / "run.json")["state"] == "cleanup_failed"
    assert not read_json(rd / "watchdog.json")["cleanup_verified"]
    assert "cleanup_failed" in (runs_dir() / "events.jsonl").read_text()


def test_unhealthy_status_breaches_required_health(env, plan):
    state, tmp = env
    plan["stop_thresholds"] = {"max_error_rate": 0.5, "required_health": "healthy"}
    tp, rd = arm(plan)
    row = fault_row("t1", errors=0, reqs=10, p95=50)
    row["health"] = "degraded"
    write_metrics(tmp, [row])
    watchdog.tick(tp, load_safety())
    assert_cleaned(tp, rd, "threshold_breach:health")


def test_p2_threshold_names_trigger_stop(env, plan):
    state, tmp = env
    plan["stop_thresholds"] = {"max_error_rate": 0.05, "max_latency_p95_ms": 500, "required_health": "healthy"}
    tp, rd = arm(plan)
    write_metrics(tmp, [fault_row("t1", errors=0, reqs=10, p95=900)])
    watchdog.tick(tp, load_safety())
    assert_cleaned(tp, rd, "threshold_breach:p95_latency")


def test_multiple_triggers_are_all_recorded(env, plan):
    """Kill switch + error-rate breach + latency breach at once: primary reason first, all listed."""
    state, tmp = env
    tp, rd = arm(plan)
    (runs_dir() / "KILL").write_text("stop")
    write_metrics(tmp, [fault_row("t1", errors=9, reqs=10, p95=9000)])
    watchdog.tick(tp, load_safety())
    wd = read_json(rd / "watchdog.json")
    assert wd["stop_reason"] == "manual_kill"
    assert set(wd["all_triggers"]) == {"manual_kill", "threshold_breach:error_rate", "threshold_breach:p95_latency"}


def test_cleanup_errors_are_kept_as_evidence(env, plan, monkeypatch):
    tp, rd = arm(plan, started=now() - 100, deadline_in=5)
    monkeypatch.setattr(tp, "remove_toxic", lambda *a: (_ for _ in ()).throw(RuntimeError("proxy down")))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    watchdog.tick(tp, load_safety())
    assert any("proxy down" in e for e in read_json(rd / "watchdog.json")["errors"])


def test_dead_runner_still_gets_a_result_for_p4(env, plan):
    """Runner crashed -> no one writes result.json -> the watchdog writes it, and releases the lock."""
    from chaos import lock
    tp, rd = arm(plan, heartbeat=False, started=now() - 60, deadline_in=300)
    assert lock.acquire(PROXY, "t1")
    watchdog.tick(tp, load_safety())
    result = read_json(rd / "result.json")
    assert result["status"] == "aborted" and result["stop_reason"] == "runner_heartbeat_lost"
    assert result["written_by"] == "watchdog" and result["cleanup_verified"]
    assert result["timeline"]["injected_at"] and result["timeline"]["removed_at"]
    assert lock.owner(PROXY) is None
    lines = (runs_dir() / "results.jsonl").read_text().splitlines()
    assert any('"t1"' in line for line in lines)


def test_watchdog_reenables_a_proxy_left_disabled(env):
    state, _ = env
    state["enabled"] = False                       # e.g. a connection-disabled run that died
    tp = Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"])
    watchdog.tick(tp, load_safety())
    assert state["enabled"] is True


def test_watchdog_cleans_up_a_connection_disabled_run(env, plan):
    state, _ = env
    plan["fault_type"] = "connection-disabled"
    rid = plan["run_id"]
    rd = run_dir(rid)
    write_json(rd / "run.json", {"plan": plan, "state": "injected", "started_at": now() - 100, "deadline": now() - 90})
    tp = Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"])
    tp.add_fault(PROXY, "connection-disabled", toxic_name(rid), 1)
    watchdog.tick(tp, load_safety())
    assert state["enabled"] is True and read_json(rd / "watchdog.json")["cleanup_verified"]
