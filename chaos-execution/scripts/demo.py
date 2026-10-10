"""One-command demo (needs the sandbox running).

  python scripts/demo.py normal      # 800 ms delay for 25 s, completes, recovers
  python scripts/demo.py threshold   # watchdog stops it early on a latency breach
  python scripts/demo.py crash       # kill the runner mid-run; the watchdog still cleans up
  python scripts/demo.py kill        # manual kill switch during the run
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from chaos.config import load_safety, read_json, runs_dir  # noqa: E402

PY = sys.executable
SAFETY = load_safety()
FRONT = SAFETY["sandbox"]["frontend_url"].rstrip("/")
TOXI = SAFETY["sandbox"]["toxiproxy_url"].rstrip("/")


def say(msg):
    print(f"\n=== {msg}", flush=True)


def latency_ms():
    t0 = time.time()
    try:
        ok = requests.post(FRONT + "/echo", json={"meter": 1}, timeout=10).status_code == 200
    except requests.RequestException:
        ok = False
    return (time.time() - t0) * 1000, ok


def toxics():
    try:
        return [t["name"] for t in requests.get(f"{TOXI}/proxies/podinfo-backend/toxics", timeout=3).json()]
    except requests.RequestException:
        return ["<toxiproxy unreachable>"]


def meter_until(proc, extra_s=0.0, action=None):
    """Print one latency line per second while proc runs (plus extra_s). action=(at_second, fn)."""
    start, done_at, acted = time.time(), None, False
    while True:
        if proc.poll() is not None and done_at is None:
            done_at = time.time()
        if done_at and time.time() - done_at >= extra_s:
            return
        el = time.time() - start
        if action and not acted and el >= action[0]:
            acted = True
            action[1]()
        ms, ok = latency_ms()
        fault = "  <- FAULT ON" if toxics() and toxics()[0].startswith("chaos-") else ""
        print(f"{time.strftime('%H:%M:%S')}  {ms:6.0f} ms  {'#' * min(50, int(ms / 20))}{'' if ok else ' ERROR'}{fault}", flush=True)
        time.sleep(max(0, 1 - ((time.time() - start) - el)))


def main(scenario):
    run_id = f"{scenario}-{time.strftime('%H%M%S')}"
    plan = {"run_id": run_id, "candidate_id": f"demo-{scenario}", "target": "podinfo-backend",
            "fault_type": "latency", "intensity": 800, "max_duration": 25,
            "stop_thresholds": {"max_error_rate": 0.5, "max_latency_p95_ms": 3000, "required_health": "healthy"},
            "reason_codes": ["DEMO"]}
    if scenario == "threshold":
        plan["max_duration"] = 40
        plan["stop_thresholds"]["max_latency_p95_ms"] = 300     # an 800 ms delay breaches this
    if scenario in ("crash", "kill"):
        plan["max_duration"] = 40
    plan_path = runs_dir() / f"{run_id}-plan.json"
    plan_path.write_text(json.dumps(plan, indent=2))

    say(f"Scenario '{scenario}', run id {run_id}. Plan: {plan['intensity']} ms delay, max {plan['max_duration']} s")
    if not latency_ms()[1]:
        sys.exit("Sandbox not reachable. Start it first (sandbox\\start.ps1).")

    say("1/4 Starting the independent watchdog (separate process)")
    watchdog = subprocess.Popen([PY, "-m", "chaos.watchdog"], cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    runner = fault = None
    try:
        say("2/4 Recording a 12 s baseline (normal traffic)")
        subprocess.run([PY, "scripts/mock_metrics.py", run_id, "baseline", "12"], cwd=ROOT, stdout=subprocess.DEVNULL)

        say("3/4 Running the experiment. Watch the latency jump while the fault is on")
        fault = subprocess.Popen([PY, "scripts/mock_metrics.py", run_id, "fault", str(plan["max_duration"] + 5)],
                                 cwd=ROOT, stdout=subprocess.DEVNULL)
        runner = subprocess.Popen([PY, "-m", "chaos.runner", str(plan_path)], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        action = None
        if scenario == "crash":
            def crash():
                print(">>> KILLING THE RUNNER PROCESS NOW (simulated crash)", flush=True)
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(runner.pid)], capture_output=True)
            action = (10, crash)
        if scenario == "kill":
            def kill():
                print(">>> MANUAL KILL SWITCH PRESSED", flush=True)
                (runs_dir() / "KILL").write_text("demo")
            action = (10, kill)
        meter_until(runner, extra_s=4, action=action)

        if scenario == "crash":
            print("\n(runner is dead; waiting for the watchdog to notice and clean up...)")
            for _ in range(20):
                if (runs_dir() / run_id / "watchdog.json").exists():
                    break
                time.sleep(1)
        say("4/4 Result")
        out = runner.communicate()[0] if runner.poll() is None or runner.stdout else ""
        for name in ("result.json", "watchdog.json"):
            data = read_json(runs_dir() / run_id / name)
            if data:
                print(f"{name}: " + json.dumps(data))
        print("faults still active on the proxy:", toxics() or "none (clean)")
    finally:
        for p in (runner, fault, watchdog):
            if p and p.poll() is None:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
        (runs_dir() / "KILL").unlink(missing_ok=True)
        (runs_dir() / "locks" / "podinfo-backend.lock").unlink(missing_ok=True)


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("normal", "threshold", "crash", "kill"):
        sys.exit(__doc__)
    main(sys.argv[1])
