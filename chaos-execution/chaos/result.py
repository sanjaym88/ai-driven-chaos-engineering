"""Writers for result.json, the all-runs index and the run event queue (P4 / P5 read these)."""
import json
import uuid

from .config import now, run_dir, runs_dir, write_json


def emit_event(kind: str, run_id: str, **fields) -> None:
    """Append-only queue. Slack delivery lives elsewhere and never gates cleanup."""
    event = {"event_id": uuid.uuid4().hex, "type": kind, "run_id": run_id, "timestamp": now(), **fields}
    with open(runs_dir() / "events.jsonl", "a", encoding="utf8") as f:
        f.write(json.dumps(event) + "\n")


def build_timeline(run: dict, finished_at: float) -> dict:
    """Epoch-second boundaries so P4 can split metrics into before / during / after windows."""
    injected, removed = run.get("started_at"), run.get("removed_at")
    return {"armed_at": run.get("armed_at"), "injected_at": injected, "removed_at": removed,
            "finished_at": finished_at,
            "fault_duration_s": round(removed - injected, 2) if injected and removed else None}


def write_result(run_id: str, status: str, stop_reason: str, cleanup_verified: bool,
                 recovery_result: str, evidence_refs: list, block_reasons: list | None = None,
                 extra: dict | None = None) -> dict:
    result = {"run_id": run_id, "status": status, "stop_reason": stop_reason,
              "cleanup_verified": cleanup_verified, "recovery_result": recovery_result,
              "evidence_refs": evidence_refs}
    if block_reasons:
        result["block_reasons"] = block_reasons
    result.update(extra or {})
    write_json(run_dir(run_id) / "result.json", result)
    with open(runs_dir() / "results.jsonl", "a", encoding="utf8") as f:   # one line per finished run, for P4
        f.write(json.dumps(result) + "\n")
    emit_event(status, run_id, stop_reason=stop_reason)
    return result
