"""Per-phase statistics and recovery time from per-request records.

A record is a dict: {run_id, seq, phase, t_send, t_rel, latency_ms, status, ok, error}.
Latency percentiles are computed over SUCCESSFUL requests only (a fast reset would otherwise make a
broken link look fast); failures are reported through success_rate and the error breakdown.
"""
from collections import Counter

PHASES = ("baseline", "fault", "recovery")
RECOVERY_CONSECUTIVE = 5          # this many consecutive good requests = "serving normally again"


def percentile(sorted_values: list, q: float):
    """Linear-interpolated percentile (same method as numpy's default). None for an empty list."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q / 100.0
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def _r(x, nd=1):
    return None if x is None else round(x, nd)


def phase_for(t_send: float, injected_at, removed_at) -> str:
    """Authoritative phase from the fault timeline (not from a polling loop)."""
    if injected_at is None or t_send < injected_at:
        return "baseline"
    if removed_at is not None and t_send >= removed_at:
        return "recovery"
    return "fault"


def label_phases(records: list, injected_at, removed_at) -> list:
    for r in records:
        r["phase"] = phase_for(r["t_send"], injected_at, removed_at)
    return records


def summarize_phase(records: list) -> dict:
    n = len(records)
    ok = [r for r in records if r["ok"]]
    lat = sorted(r["latency_ms"] for r in ok)
    return {
        "count": n,
        "success_count": len(ok),
        "success_rate": _r(len(ok) / n, 4) if n else None,
        "p50_ms": _r(percentile(lat, 50)),
        "p95_ms": _r(percentile(lat, 95)),
        "p99_ms": _r(percentile(lat, 99)),
        "mean_ms": _r(sum(lat) / len(lat)) if lat else None,
        "max_ms": _r(lat[-1]) if lat else None,
        "errors": dict(Counter(r["error"] for r in records if not r["ok"])),
    }


def recovery_limit_ms(baseline_p95) -> float:
    """'Normal again' = within 1.5x the baseline p95 (and at least +25 ms, so tiny baselines aren't brittle)."""
    if baseline_p95 is None:
        return 250.0
    return max(baseline_p95 * 1.5, baseline_p95 + 25.0)


def recovery(records: list, removed_at, baseline_p95, k: int = RECOVERY_CONSECUTIVE) -> dict:
    """Seconds from fault removal until the first of k consecutive good, fast-enough requests."""
    limit = recovery_limit_ms(baseline_p95)
    out = {"recovered": False, "recovery_time_s": None, "latency_limit_ms": _r(limit), "consecutive_required": k}
    if removed_at is None:
        return out
    run = []
    for r in sorted((r for r in records if r["t_send"] >= removed_at), key=lambda r: r["t_send"]):
        if r["ok"] and r["latency_ms"] <= limit:
            run.append(r)
            if len(run) == k:
                out.update(recovered=True, recovery_time_s=_r(max(0.0, run[0]["t_send"] - removed_at), 2))
                return out
        else:
            run = []
    return out


def summarize_case(records: list, timeline: dict) -> dict:
    label_phases(records, timeline.get("injected_at"), timeline.get("removed_at"))
    phases = {p: summarize_phase([r for r in records if r["phase"] == p]) for p in PHASES}
    rec = recovery(records, timeline.get("removed_at"), phases["baseline"]["p95_ms"])
    return {"phases": phases, "recovery": rec}
