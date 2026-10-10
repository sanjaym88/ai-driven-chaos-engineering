"""Strict plan validation. Nothing touches Toxiproxy unless validate_plan() and preflight() both pass."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import lock
from .config import now, parse_ts, runs_dir

REQUIRED = ["run_id", "candidate_id", "target", "fault_type", "intensity",
            "max_duration", "stop_thresholds", "reason_codes"]
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


@dataclass
class Verdict:
    reasons: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.reasons


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


THRESHOLD_ALIASES = {"max_error_rate": "error_rate", "max_latency_p95_ms": "p95_latency_ms"}
HEALTHY = ("healthy", "ok")


def normalize_thresholds(th) -> dict:
    """Accept P2's names (max_error_rate, max_latency_p95_ms, required_health) and ours."""
    if not isinstance(th, dict):
        return {}
    return {THRESHOLD_ALIASES.get(k, k): v for k, v in th.items()}


def validate_plan(plan: dict, safety: dict, service_map: dict) -> Verdict:
    v = Verdict()
    missing = [k for k in REQUIRED if k not in plan]
    if missing:
        v.reasons.append(f"missing fields: {', '.join(missing)}")
        return v
    if not isinstance(plan["run_id"], str) or not RUN_ID.match(plan["run_id"]):
        v.reasons.append("invalid run_id")

    proxy_names = {s.get("proxy_name") for s in service_map.get("services", []) if s.get("proxy_name")}
    allowed = safety["targets"].get(plan["target"])
    if allowed is None or plan["target"] not in proxy_names:
        v.reasons.append(f"target not allowed or not in service map: {plan['target']}")
    elif plan["fault_type"] not in allowed["allowed_faults"]:
        v.reasons.append(f"fault not allowed for target: {plan['fault_type']}")

    fault = safety["faults"].get(plan["fault_type"])
    if fault is None:
        v.reasons.append(f"unsupported fault type: {plan['fault_type']}")
    elif not _num(plan["intensity"]) or not fault.get("min_intensity", 1) <= plan["intensity"] <= fault["max_intensity"]:
        v.reasons.append(f"intensity must be in [{fault.get('min_intensity', 1)}, {fault['max_intensity']}] {fault['unit']}")

    lim = safety["limits"]
    if not _num(plan["max_duration"]) or not 0 < plan["max_duration"] <= lim["max_duration_s"]:
        v.reasons.append(f"max_duration must be in (0, {lim['max_duration_s']}] s")

    th = normalize_thresholds(plan["stop_thresholds"])
    caps = {"error_rate": lim["max_error_rate"], "p95_latency_ms": lim["max_p95_latency_ms"]}
    numeric = {k: val for k, val in th.items() if k != "required_health"}
    if not numeric or set(numeric) - set(caps):
        v.reasons.append("stop_thresholds must set error_rate and/or p95_latency_ms")
    else:
        for k, val in numeric.items():
            if not _num(val) or not 0 < val <= caps[k]:
                v.reasons.append(f"stop threshold {k} must be in (0, {caps[k]}]")
    if "required_health" in th and th["required_health"] not in HEALTHY:
        v.reasons.append("required_health must be 'healthy'")
    if not isinstance(plan["reason_codes"], list):
        v.reasons.append("reason_codes must be a list")
    return v


def read_metrics(path, run_id: str, phase: str) -> list:
    rows = []
    try:
        lines = Path(path).read_text(encoding="utf8").splitlines()
    except FileNotFoundError:
        return rows
    for line in lines:
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and r.get("run_id") == run_id and r.get("phase") == phase:
            rows.append(r)
    return rows


def baseline_ok(plan: dict, safety: dict, t: float | None = None) -> str | None:
    """None if a fresh, sufficient baseline exists for this run, else the reason."""
    t = t or now()
    rows = read_metrics(safety["sandbox"]["metrics_path"], plan["run_id"], "baseline")
    if not rows:
        return "baseline missing"
    try:
        newest = max(parse_ts(r["timestamp"]) for r in rows)
    except (KeyError, ValueError):
        return "baseline timestamps unreadable"
    if t - newest > safety["limits"]["baseline_max_age_s"]:
        return "baseline stale"
    total = sum(r.get("request_count", 0) for r in rows)
    if total < safety["limits"]["baseline_min_requests"]:
        return "baseline has too few requests"
    if sum(r.get("errors", 0) for r in rows) / total > safety["limits"].get("baseline_max_error_rate", 1.0):
        return "baseline unhealthy (too many errors)"
    return None


def watchdog_alive(safety: dict, t: float | None = None) -> bool:
    try:
        ts = float((runs_dir() / "watchdog.alive").read_text())
    except (FileNotFoundError, ValueError):
        return False
    return (t or now()) - ts <= safety["watchdog"]["alive_timeout_s"]


def preflight(plan: dict, safety: dict) -> Verdict:
    """Dynamic prerequisites. Any failure -> BLOCKED, no fault injected."""
    v = Verdict()
    if (runs_dir() / plan["run_id"] / "run.json").exists() or (runs_dir() / plan["run_id"] / "result.json").exists():
        v.reasons.append("run_id already used; generate a new plan")
    reason = baseline_ok(plan, safety)
    if reason:
        v.reasons.append(reason)
    if not watchdog_alive(safety):
        v.reasons.append("watchdog unavailable")
    if (runs_dir() / "KILL").exists():
        v.reasons.append("kill switch is set (run scripts/reset.ps1)")
    holder = lock.owner(plan["target"])
    if holder:
        v.reasons.append(f"target busy: held by run {holder}")
    return v
