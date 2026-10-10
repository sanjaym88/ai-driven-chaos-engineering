"""Chaos Toolkit actions. They take only a run_id and read the already-validated plan from disk,
so the toolkit can never be handed unvalidated fault parameters."""
from .config import load_safety, now, read_json, run_dir, write_json
from .probes import hold_fault as _hold
from .toxiproxy import Toxiproxy, toxic_name


def _tp() -> Toxiproxy:
    return Toxiproxy(load_safety()["sandbox"]["toxiproxy_url"])


def _load(run_id: str):
    path = run_dir(run_id) / "run.json"
    return path, read_json(path)


def inject_fault(run_id: str) -> bool:
    path, run = _load(run_id)
    plan = run["plan"]
    # State and deadline are recorded BEFORE the fault exists, so the watchdog already covers it.
    run.update(state="injecting", started_at=now(), deadline=now() + plan["max_duration"])
    write_json(path, run)
    _tp().add_fault(plan["target"], plan["fault_type"], toxic_name(run_id), plan["intensity"])
    run["state"] = "injected"
    write_json(path, run)
    return True


inject_latency = inject_fault   # backwards-compatible name


def hold_fault(run_id: str) -> str:
    _, run = _load(run_id)
    reason = _hold(run_id, run["plan"]["max_duration"])
    path, run = _load(run_id)
    run.setdefault("stop_reason", reason)
    write_json(path, run)
    return reason


def remove_fault(run_id: str) -> bool:
    """Rollback. Idempotent, and verifies the toxic is really gone."""
    path, run = _load(run_id)
    plan = run["plan"]
    tp = _tp()
    tp.remove_fault(plan["target"], plan["fault_type"], toxic_name(run_id))
    clean = tp.is_clean(plan["target"], toxic_name(run_id))
    run = read_json(path)
    if clean:
        run.setdefault("removed_at", now())
        if run.get("state") in ("injecting", "injected"):
            run["state"] = "stopped"
        write_json(path, run)
    return clean
