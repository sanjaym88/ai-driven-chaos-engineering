import json

import pytest

from chaos.campaign import FAULT_MATRIX, build_cases
from chaos.config import load_safety, load_service_map
from chaos.stats import percentile, phase_for, recovery, summarize_case, summarize_phase
from chaos.validator import validate_plan


def rec(t, lat, ok=True, error=None, phase="baseline"):
    return {"run_id": "r", "seq": int(t * 10), "phase": phase, "t_send": t, "t_rel": t,
            "latency_ms": lat, "status": 200 if ok else None, "ok": ok, "error": error}


def test_percentile_matches_known_values():
    v = sorted(range(1, 101))                       # 1..100
    assert percentile(v, 50) == 50.5
    assert percentile(v, 95) == pytest.approx(95.05)
    assert percentile(v, 99) == pytest.approx(99.01)
    assert percentile([7], 99) == 7 and percentile([], 50) is None


def test_phase_summary_counts_success_rate_and_percentiles():
    recs = [rec(i, 10 + i) for i in range(8)] + [rec(9, 5, ok=False, error="timeout"), rec(10, 6, ok=False, error="timeout")]
    s = summarize_phase(recs)
    assert s["count"] == 10 and s["success_count"] == 8 and s["success_rate"] == 0.8
    assert s["errors"] == {"timeout": 2}
    assert s["p50_ms"] == 13.5                      # failures (5, 6 ms) are excluded from latency percentiles
    assert s["max_ms"] == 17


def test_empty_phase_has_no_percentiles():
    s = summarize_phase([])
    assert s["count"] == 0 and s["success_rate"] is None and s["p95_ms"] is None


def test_phase_boundaries_come_from_the_fault_timeline():
    assert phase_for(9.9, 10, 20) == "baseline"
    assert phase_for(10, 10, 20) == "fault" and phase_for(19.9, 10, 20) == "fault"
    assert phase_for(20, 10, 20) == "recovery"
    assert phase_for(5, None, None) == "baseline"


def test_recovery_time_is_measured_from_fault_removal():
    # fault removed at t=20; slow until 20.5, then 5 good requests starting at 20.6
    recs = [rec(20.1, 900), rec(20.3, 900), rec(20.4, 60, ok=False, error="timeout"), rec(20.5, 900)]
    recs += [rec(20.6 + 0.1 * i, 12) for i in range(8)]
    r = recovery(recs, removed_at=20.0, baseline_p95=12)
    assert r["recovered"] and r["recovery_time_s"] == 0.6


def test_recovery_streak_resets_on_a_bad_request():
    good = [rec(20.1 + 0.1 * i, 12) for i in range(4)]            # only 4 good...
    recs = good + [rec(20.5, 12, ok=False, error="x")] + [rec(20.6 + 0.1 * i, 12) for i in range(5)]
    r = recovery(recs, removed_at=20.0, baseline_p95=12)
    assert r["recovered"] and r["recovery_time_s"] == 0.6          # ...so the streak starts after the failure


def test_never_recovered():
    recs = [rec(21 + i, 900) for i in range(10)]
    r = recovery(recs, removed_at=20.0, baseline_p95=12)
    assert r == {"recovered": False, "recovery_time_s": None, "latency_limit_ms": 37.0, "consecutive_required": 5}


def test_summarize_case_splits_phases_and_reports_recovery():
    recs = [rec(i * 0.1, 10) for i in range(50)]                    # t 0..4.9 baseline
    recs += [rec(5 + i * 0.1, 500) for i in range(50)]              # t 5..9.9 fault
    recs += [rec(10 + i * 0.1, 10) for i in range(30)]              # t 10.. recovery
    out = summarize_case(recs, {"injected_at": 5.0, "removed_at": 10.0})
    p = out["phases"]
    assert (p["baseline"]["count"], p["fault"]["count"], p["recovery"]["count"]) == (50, 50, 30)
    assert p["fault"]["p95_ms"] == 500 and p["baseline"]["p95_ms"] == 10
    assert out["recovery"]["recovered"] and out["recovery"]["recovery_time_s"] == 0.0


def test_campaign_has_100_to_200_cases_and_every_plan_passes_validation():
    cases = build_cases("t", repeats=7, duration_s=5)
    assert 100 <= len(cases) <= 200
    assert len({c["run_id"] for c in cases}) == len(cases)           # unique run ids
    safety, smap = load_safety(), load_service_map()
    bad = [(c["run_id"], validate_plan(c["plan"], safety, smap).reasons) for c in cases
           if not validate_plan(c["plan"], safety, smap).ok]
    assert bad == []
    assert {c["fault_type"] for c in cases} == {f for f, _ in FAULT_MATRIX}
