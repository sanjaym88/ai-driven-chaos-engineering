import copy
import json
from pathlib import Path

import pytest

from chaos import lock
from chaos.config import ROOT, load_safety, load_service_map, now, runs_dir
from chaos.validator import preflight, validate_plan
from conftest import good_baseline, write_metrics

SAMPLES = ROOT / "contracts" / "samples"


def load(name):
    return json.loads((SAMPLES / name).read_text(encoding="utf8"))


def check(plan):
    return validate_plan(plan, load_safety(), load_service_map())


def test_sample_plan_is_accepted():
    assert check(load("experiment-plan.sample.json")).ok


@pytest.mark.parametrize("sample,expected", [
    ("plan-bad-target.json", "target"),
    ("plan-bad-fault.json", "fault"),
    ("plan-over-duration.json", "max_duration"),
])
def test_bad_plans_are_rejected(sample, expected):
    v = check(load(sample))
    assert not v.ok and any(expected in r for r in v.reasons)


@pytest.mark.parametrize("field,value", [
    ("intensity", 99999), ("intensity", -5), ("intensity", "500"), ("max_duration", 0),
    ("run_id", "../evil"), ("stop_thresholds", {"error_rate": 0.99}), ("stop_thresholds", {}),
    ("stop_thresholds", {"cpu": 1}),
])
def test_out_of_bounds_values_rejected(field, value):
    plan = load("experiment-plan.sample.json")
    plan[field] = value
    assert not check(plan).ok


def test_missing_field_rejected():
    plan = load("experiment-plan.sample.json")
    del plan["stop_thresholds"]
    assert not check(plan).ok


def test_preflight_blocks_without_baseline_or_watchdog(env, plan):
    v = preflight(plan, load_safety())
    assert "baseline missing" in v.reasons and "watchdog unavailable" in v.reasons


def test_preflight_blocks_stale_baseline(env, plan):
    state, tmp = env
    write_metrics(tmp, [good_baseline("t1", age=10_000)])
    (runs_dir() / "watchdog.alive").write_text(str(now()))
    assert preflight(plan, load_safety()).reasons == ["baseline stale"]


def test_preflight_passes_then_blocks_when_target_busy(env, plan):
    state, tmp = env
    write_metrics(tmp, [good_baseline("t1")])
    (runs_dir() / "watchdog.alive").write_text(str(now()))
    assert preflight(plan, load_safety()).ok
    assert lock.acquire(plan["target"], "other-run")
    assert any("busy" in r for r in preflight(plan, load_safety()).reasons)


def test_p2_plan_is_accepted():
    """The real plan P2 sent: max_error_rate / max_latency_p95_ms / required_health."""
    assert check(load("p2-plan.json")).ok


def test_p2_threshold_over_cap_or_bad_health_rejected():
    plan = load("p2-plan.json")
    plan["stop_thresholds"]["max_latency_p95_ms"] = 99999
    assert not check(plan).ok
    plan = load("p2-plan.json")
    plan["stop_thresholds"]["required_health"] = "whatever"
    assert not check(plan).ok


def test_json_helpers_survive_concurrent_read_write(tmp_path):
    """Windows raises PermissionError when replacing a file that is being read; helpers must retry."""
    import threading, time
    from chaos.config import read_json, write_json
    p = tmp_path / "hb.json"
    write_json(p, {"t": 0})
    stop, errors = threading.Event(), []

    def reader():
        while not stop.is_set():
            try:
                read_json(p)
            except Exception as e:
                errors.append(e)

    def writer():
        while not stop.is_set():
            try:
                write_json(p, {"t": time.time()})
            except Exception as e:
                errors.append(e)

    threads = [threading.Thread(target=reader) for _ in range(3)] + [threading.Thread(target=writer)]
    [t.start() for t in threads]
    time.sleep(2)
    stop.set()
    [t.join() for t in threads]
    assert errors == []


def test_baseline_full_of_errors_blocks_the_run(env, plan):
    state, tmp = env
    bad = good_baseline("t1")
    bad["errors"] = 20                      # 20 of 30 requests failed during the baseline
    write_metrics(tmp, [bad])
    (runs_dir() / "watchdog.alive").write_text(str(now()))
    assert "baseline unhealthy (too many errors)" in preflight(plan, load_safety()).reasons
