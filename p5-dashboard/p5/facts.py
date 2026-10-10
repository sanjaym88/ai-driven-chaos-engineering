"""Facts = every number the report, dashboard and Slack message are allowed to show.

Each fact is computed here, once, from the run records, and carries a `source` string
saying exactly how it was derived. Templates only print facts, so "report numbers
match the source records" holds by construction (and tests/test_facts.py re-checks it
with an independent calculation).

Phase summary definitions (agree these with P4 at merge!):
  requests     = sum of request_count in the phase
  errors       = sum of errors in the phase
  error_rate   = errors / requests
  p50_ms       = median of the interval p50 values
  p95_ms       = worst (max) interval p95 in the phase
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass

from .adapters import PHASES, Run


@dataclass(frozen=True)
class Fact:
    value: float | int | str | bool | None
    source: str
    unit: str = ""

    @property
    def known(self) -> bool:
        return self.value is not None


def fmt(f: Fact | None, digits: int = 0) -> str:
    """The ONLY formatter used by templates. None -> 'unknown'."""
    if f is None or f.value is None:
        return "unknown"
    v = f.value
    if isinstance(v, bool):
        return "yes" if v else "no"
    if f.unit == "%":
        return f"{v * 100:.1f}%"
    if isinstance(v, float):
        return f"{v:.{digits}f}{(' ' + f.unit) if f.unit else ''}"
    return f"{v}{(' ' + f.unit) if f.unit else ''}"


def phase_summary(run: Run, phase: str) -> dict[str, Fact]:
    s = [x for x in run.samples if x.phase == phase]
    src = f"metrics.jsonl phase={phase} ({len(s)} lines)"
    if not s:
        none = Fact(None, f"no {phase} samples in metrics.jsonl")
        return {"requests": none, "errors": none, "error_rate": none, "p50_ms": none, "p95_ms": none,
                "samples": Fact(0, src)}
    req = sum(x.requests for x in s)
    err = sum(x.errors for x in s)
    p50s = [x.p50_ms for x in s if x.p50_ms is not None]
    p95s = [x.p95_ms for x in s if x.p95_ms is not None]
    return {
        "samples": Fact(len(s), src),
        "requests": Fact(req, f"sum(request_count) {src}"),
        "errors": Fact(err, f"sum(errors) {src}"),
        "error_rate": Fact(err / req if req else None, f"sum(errors)/sum(request_count) {src}", "%"),
        "p50_ms": Fact(round(statistics.median(p50s), 1) if p50s else None, f"median(latency.p50_ms) {src}", "ms"),
        "p95_ms": Fact(round(max(p95s), 1) if p95s else None, f"max(latency.p95_ms) {src}", "ms"),
    }


def build_facts(run: Run) -> dict:
    p = run.plan
    phases = {ph: phase_summary(run, ph) for ph in PHASES}
    fault_s = None
    if run.fault_started_at and run.fault_ended_at:
        fault_s = (run.fault_ended_at - run.fault_started_at).total_seconds()
    thr = (p.stop_thresholds if p else {}) or {}
    return {
        "run_id": run.run_id,
        "status": run.status or "unknown",
        "target": (p.target if p else None) or "unknown",
        "fault_type": (p.fault_type if p else None) or "unknown",
        "intensity": ", ".join(f"{k}={v}" for k, v in (p.intensity if p else {}).items()) or "unknown",
        "candidate_id": (p.candidate_id if p else None) or "unknown",
        "reason_codes": (p.reason_codes if p else []) or [],
        "planner": (p.planner if p else None) or "unknown",
        "score": Fact(p.score if p else None, "plan.json score"),
        "rank": Fact(p.rank if p else None, "plan.json rank"),
        "max_duration_s": Fact(p.max_duration_s if p else None, "plan.json max_duration_s", "s"),
        "threshold_p95_ms": Fact(thr.get("p95_ms"), "plan.json stop_thresholds.p95_ms", "ms"),
        "threshold_error_rate": Fact(thr.get("error_rate"), "plan.json stop_thresholds.error_rate", "%"),
        "fault_duration_s": Fact(fault_s, "result.json fault_ended_at - fault_started_at", "s"),
        "stop_reason": run.stop_reason or "unknown",
        "stopped_by": run.stopped_by,
        "cleanup_verified": Fact(run.cleanup_verified, "result.json cleanup_verified"),
        "recovery_status": run.recovery_status or "unknown",
        "recovery_time_s": Fact(run.recovery_time_s, "result.json recovery_result.recovery_time_s", "s"),
        "failure_detail": run.failure_detail,
        "started_at": run.started_at.strftime("%Y-%m-%d %H:%M:%S UTC") if run.started_at else "unknown",
        "phases": phases,
        "evidence": run.evidence,
        "problems": run.problems,
    }
