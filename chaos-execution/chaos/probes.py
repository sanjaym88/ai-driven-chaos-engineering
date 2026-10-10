"""Health / recovery probes and the hold step. Used by Chaos Toolkit, the runner and the watchdog."""
import time

import requests

from .config import load_safety, now, read_json, run_dir


def frontend_healthy(url: str | None = None) -> bool:
    url = url or load_safety()["sandbox"]["frontend_url"]
    try:
        return requests.get(url.rstrip("/") + "/healthz", timeout=3).status_code == 200
    except requests.RequestException:
        return False


def echo_ok(url: str | None = None, timeout: float = 10.0) -> bool:
    """The real tested path: POST /echo goes frontend -> Toxiproxy -> backend (see service-map.json)."""
    url = url or load_safety()["sandbox"]["frontend_url"]
    try:
        r = requests.post(url.rstrip("/") + "/echo", json={"chaos_probe": "ok"}, timeout=timeout)
        return r.status_code == 200 and "chaos_probe" in r.text
    except requests.RequestException:
        return False


def wait_for_recovery(safety: dict | None = None) -> str:
    """'recovered' once health and the echo path both succeed inside the bounded window."""
    safety = safety or load_safety()
    rec = safety["recovery"]
    deadline = now() + rec["timeout_s"]
    while now() < deadline:
        if frontend_healthy() and echo_ok():
            return "recovered"
        time.sleep(rec["poll_interval_s"])
    return "not_recovered"


def hold_fault(run_id: str, max_duration: float) -> str:
    """Keep the fault applied until max_duration, or until the watchdog/kill switch stops the run."""
    rd = run_dir(run_id)
    end = now() + float(max_duration)
    while now() < end:
        state = (read_json(rd / "run.json") or {}).get("state")
        if state not in ("injecting", "injected"):
            return "stopped_externally"
        time.sleep(0.5)
    return "duration_expired"
