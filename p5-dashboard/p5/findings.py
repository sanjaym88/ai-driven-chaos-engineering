"""Rule-based findings: a fixed, versioned table. No LLM, no guessing.

Each rule says WHEN it applies (using facts only), WHAT it found (sentence built from
facts) and the FIX. If a rule needs a fact that is unknown, it reports "unknown"
instead of a conclusion ("unknown stays unknown").

To add a rule: append a Rule to RULES and bump RULES_VERSION.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .facts import fmt

RULES_VERSION = "findings-rules v1"


@dataclass
class Finding:
    rule_id: str
    severity: str          # critical | high | medium | low | info | unknown
    title: str
    detail: str
    fix: str


@dataclass
class Rule:
    rule_id: str
    needs: list[str]                       # fact paths that must be known, e.g. "fault.p95_ms"
    applies: Callable[[dict], bool]
    build: Callable[[dict], Finding]


def _get(f: dict, path: str):
    """'fault.p95_ms' -> facts['phases']['fault']['p95_ms']; 'recovery_time_s' -> facts[...]"""
    if "." in path:
        ph, key = path.split(".", 1)
        return f["phases"][ph][key]
    return f[path]


def _val(f, path):
    x = _get(f, path)
    return getattr(x, "value", x)


def _latency_ms(f) -> float | None:
    s = f["intensity"]
    for part in s.split(", "):
        if part.startswith("latency_ms="):
            return float(part.split("=", 1)[1])
    return None


RULES: list[Rule] = [
    Rule("F0_BLOCKED", [],
         lambda f: f["status"] == "blocked",
         lambda f: Finding("F0_BLOCKED", "info", "Run blocked before injection - nothing was touched",
                           f"Pre-check failed: {f['stop_reason']}. No fault was injected, so there is no resilience finding for this run.",
                           "Fix the failed precondition (fresh baseline, healthy watchdog, valid plan) and re-run.")),
    Rule("F1_CLEANUP_UNVERIFIED", ["cleanup_verified"],
         lambda f: f["status"] != "blocked" and _val(f, "cleanup_verified") is False,
         lambda f: Finding("F1_CLEANUP_UNVERIFIED", "critical", "Fault cleanup could not be verified",
                           f"{(f['failure_detail'] or 'Cleanup check failed').rstrip('.')}. The sandbox may still be degraded.",
                           "Reset Toxiproxy (POST /reset) or restart the proxy container, confirm no toxics remain, then re-check app health before any new run.")),
    Rule("F2_NOT_RECOVERED", [],
         lambda f: f["recovery_status"] == "not_recovered",
         lambda f: Finding("F2_NOT_RECOVERED", "critical", "Application did not recover in the verification window",
                           f"After the fault was removed, recovery error rate stayed at {fmt(f['phases']['recovery']['error_rate'])} "
                           f"(baseline {fmt(f['phases']['baseline']['error_rate'])}).",
                           "Check that the fault is really removed; add readiness checks and connection re-establishment (fresh connections after reset) in the frontend's backend client.")),
    Rule("F3_ERRORS_REACH_USERS", ["fault.error_rate", "baseline.error_rate"],
         lambda f: _val(f, "fault.error_rate") - _val(f, "baseline.error_rate") >= 0.05,
         lambda f: Finding("F3_ERRORS_REACH_USERS", "high", f"Failures on {f['target']} reach users as errors",
                           f"Error rate rose from {fmt(f['phases']['baseline']['error_rate'])} to {fmt(f['phases']['fault']['error_rate'])} during the fault.",
                           "Add retries with exponential backoff and a circuit breaker on the frontend->backend call. Note: Podinfo /echo returns HTTP 200 even when the backend fails - return a proper 5xx so failures are visible.")),
    Rule("F4_THRESHOLD_STOP", [],
         lambda f: f["status"] == "aborted" and str(f["stop_reason"]).startswith("threshold_breach"),
         lambda f: Finding("F4_THRESHOLD_STOP", "high", "Impact exceeded the safe limit; the watchdog stopped the fault",
                           f"{f['stop_reason']} (stopped by {f['stopped_by'] or 'unknown'}, after {fmt(f['fault_duration_s'])} of fault).",
                           "The system has no protection against this level of degradation. Add a client timeout below the latency budget so slow dependencies fail fast instead of stalling users.")),
    Rule("F5_LATENCY_PROPAGATES", ["fault.p95_ms", "baseline.p95_ms"],
         lambda f: f["fault_type"] == "latency" and _latency_ms(f) is not None
                   and _val(f, "fault.p95_ms") - _val(f, "baseline.p95_ms") >= 0.8 * _latency_ms(f),
         lambda f: Finding("F5_LATENCY_PROPAGATES", "medium", "Dependency delay passes straight through to users",
                           f"Injected {_latency_ms(f):.0f} ms on {f['target']}; user p95 went from {fmt(f['phases']['baseline']['p95_ms'], 1)} to {fmt(f['phases']['fault']['p95_ms'], 1)}.",
                           "Set a short client timeout on the frontend (Podinfo --http-client-timeout, default 2m), serve a cached/fallback response, and call the backend asynchronously where possible.")),
    Rule("F6_SLOW_RECOVERY", [],
         lambda f: f["recovery_status"] == "recovered" and (_val(f, "recovery_time_s") or 0) > 10,
         lambda f: Finding("F6_SLOW_RECOVERY", "medium", "Slow recovery after the fault was removed",
                           f"Back to baseline after {fmt(f['recovery_time_s'], 1)}.",
                           "Shorten connection/keep-alive reuse after failures and tighten readiness probe intervals.")),
    Rule("F7_TOLERATED", ["fault.p95_ms", "baseline.p95_ms", "fault.error_rate", "baseline.error_rate"],
         lambda f: f["status"] == "completed"
                   and _val(f, "fault.error_rate") - _val(f, "baseline.error_rate") < 0.01
                   and _val(f, "fault.p95_ms") <= 1.2 * _val(f, "baseline.p95_ms") + 50,
         lambda f: Finding("F7_TOLERATED", "info", "No measurable user impact",
                           f"p95 {fmt(f['phases']['fault']['p95_ms'], 1)} vs baseline {fmt(f['phases']['baseline']['p95_ms'], 1)}; error rate {fmt(f['phases']['fault']['error_rate'])}.",
                           "None needed for this fault level; consider a stronger intensity next.")),
]

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4, "unknown": 5}


def evaluate(facts: dict) -> list[Finding]:
    out: list[Finding] = []
    for rule in RULES:
        missing = [n for n in rule.needs if _val(facts, n) is None]
        if missing:
            # Only report "unknown" for rules that matter for a run that injected something
            if facts["status"] not in ("blocked", "unknown"):
                out.append(Finding(rule.rule_id, "unknown", f"{rule.rule_id}: cannot be evaluated",
                                   f"Missing data: {', '.join(missing)}.", "Check the run's records (P3/P4)."))
            continue
        try:
            if rule.applies(facts):
                out.append(rule.build(facts))
        except (TypeError, ZeroDivisionError):
            out.append(Finding(rule.rule_id, "unknown", f"{rule.rule_id}: cannot be evaluated",
                               "Records are incomplete for this rule.", "Check the run's records (P3/P4)."))
    return sorted(out, key=lambda x: SEVERITY_ORDER[x.severity])
