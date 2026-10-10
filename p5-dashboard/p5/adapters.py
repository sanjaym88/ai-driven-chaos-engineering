"""THE ONLY FILE THAT KNOWS THE RAW FIELD NAMES OF THE TEAM'S FILES.

Everything else in P5 uses the clean objects below (Run, Plan, Sample, ServiceMap).
At merge time, if P1-P4 name a field differently (e.g. `latency_p95` instead of
`latency.p95_ms`, or `state` instead of `status`), change it HERE and nowhere else.

Loading never crashes: missing files or fields become None and are listed in
`run.problems`, so the dashboard can show "unknown" instead of failing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ("completed", "aborted", "failed", "blocked")
PHASES = ("baseline", "fault", "recovery")


@dataclass
class Sample:
    t: datetime
    phase: str
    requests: int
    errors: int
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    health: dict


@dataclass
class Plan:
    candidate_id: str | None
    target: str | None
    fault_type: str | None
    intensity: dict
    max_duration_s: float | None
    stop_thresholds: dict
    reason_codes: list[str]
    score: float | None
    rank: int | None
    planner: str | None


@dataclass
class Run:
    run_id: str
    folder: Path
    status: str | None
    started_at: datetime | None
    ended_at: datetime | None
    fault_started_at: datetime | None
    fault_ended_at: datetime | None
    stop_reason: str | None
    stopped_by: str | None
    cleanup_verified: bool | None
    recovery_status: str | None
    recovery_time_s: float | None
    failure_detail: str | None
    evidence: list[dict]
    plan: Plan | None
    samples: list[Sample]
    problems: list[str] = field(default_factory=list)

    @property
    def final(self) -> bool:
        return self.status in STATUSES


@dataclass
class ServiceMap:
    services: list[dict]
    edges: list[dict]
    request_paths: list[dict]
    notes: list[str]
    source: str | None
    reviewed_by: str | None


# ------------------------------------------------------------------ helpers
def parse_time(v) -> datetime | None:
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):                    # epoch seconds also accepted
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(v).replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(v):
    return None if v is None else float(v)


def _read_json(path: Path, problems: list[str], label: str):
    if not path.exists():
        problems.append(f"{label} missing")
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        problems.append(f"{label} is not valid JSON ({e.msg} at line {e.lineno})")
        return None


# ------------------------------------------------------------------ adapters
def adapt_plan(raw: dict | None) -> Plan | None:
    if not raw:
        return None
    return Plan(
        candidate_id=raw.get("candidate_id"),
        target=raw.get("target"),
        fault_type=raw.get("fault_type"),
        intensity=raw.get("intensity") or {},
        max_duration_s=_num(raw.get("max_duration_s", raw.get("max_duration"))),
        stop_thresholds=raw.get("stop_thresholds") or {},
        reason_codes=list(raw.get("reason_codes") or []),
        score=_num(raw.get("score")),
        rank=raw.get("rank"),
        planner=raw.get("planner"),
    )


def adapt_sample(raw: dict) -> Sample:
    lat = raw.get("latency")
    if isinstance(lat, dict):
        p50, p95, mx = lat.get("p50_ms"), lat.get("p95_ms"), lat.get("max_ms")
    else:                                   # plain number = p95 in ms
        p50, p95, mx = None, lat, None
    return Sample(
        t=parse_time(raw["timestamp"]),
        phase=raw.get("phase"),
        requests=int(raw.get("request_count", 0)),
        errors=int(raw.get("errors", 0)),
        p50_ms=_num(p50), p95_ms=_num(p95), max_ms=_num(mx),
        health=raw.get("health") or {},
    )


def load_run(folder: Path) -> Run:
    problems: list[str] = []
    res = _read_json(folder / "result.json", problems, "result.json") or {}
    plan = adapt_plan(_read_json(folder / "plan.json", problems, "plan.json"))

    samples: list[Sample] = []
    mpath = folder / "metrics.jsonl"
    if mpath.exists():
        for n, text in enumerate(mpath.read_text().splitlines(), 1):
            if not text.strip():
                continue
            try:
                samples.append(adapt_sample(json.loads(text)))
            except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
                problems.append(f"metrics.jsonl line {n} skipped ({type(e).__name__})")
        samples.sort(key=lambda s: s.t)
    else:
        problems.append("metrics.jsonl missing")

    rec = res.get("recovery_result") or {}
    status = res.get("status")
    if status is not None and status not in STATUSES:
        problems.append(f"unknown status '{status}'")
    return Run(
        run_id=res.get("run_id") or folder.name,
        folder=folder,
        status=status,
        started_at=parse_time(res.get("started_at")),
        ended_at=parse_time(res.get("ended_at")),
        fault_started_at=parse_time(res.get("fault_started_at")),
        fault_ended_at=parse_time(res.get("fault_ended_at")),
        stop_reason=res.get("stop_reason"),
        stopped_by=res.get("stopped_by"),
        cleanup_verified=res.get("cleanup_verified"),
        recovery_status=rec.get("status"),
        recovery_time_s=_num(rec.get("recovery_time_s")),
        failure_detail=res.get("failure_detail"),
        evidence=list(res.get("evidence_refs") or []),
        plan=plan,
        samples=samples,
        problems=problems,
    )


def load_runs(runs_dir: Path) -> list[Run]:
    if not runs_dir.exists():
        return []
    runs = [load_run(d) for d in sorted(runs_dir.iterdir()) if d.is_dir()]
    return sorted(runs, key=lambda r: r.started_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def load_map(path: Path) -> ServiceMap | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    return ServiceMap(
        services=raw.get("services", []),
        edges=raw.get("edges", []),
        request_paths=raw.get("request_paths", []),
        notes=list(raw.get("notes") or []),
        source=raw.get("source"),
        reviewed_by=raw.get("reviewed_by"),
    )
