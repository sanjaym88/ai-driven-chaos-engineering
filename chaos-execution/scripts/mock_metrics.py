"""Stand-in for P4's traffic generator, so you can run a real experiment before P4's code exists.

  python scripts/mock_metrics.py run-001 baseline 30     # write a baseline for run-001
  python scripts/mock_metrics.py run-001 fault 30        # run in a second terminal during the fault

Sends POST /echo to the frontend about 5x/s and appends one metrics.jsonl row every 5 seconds.
"""
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chaos.config import load_safety  # noqa: E402


def main(run_id: str, phase: str, seconds: float) -> None:
    safety = load_safety()
    url = safety["sandbox"]["frontend_url"].rstrip("/") + "/echo"
    out = Path(safety["sandbox"]["metrics_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    end = time.time() + seconds
    while time.time() < end:
        window_end, lat, errors = time.time() + 5, [], 0
        while time.time() < window_end and time.time() < end:
            t0 = time.time()
            try:
                ok = requests.post(url, json={"mock": 1}, timeout=10).status_code == 200
            except requests.RequestException:
                ok = False
            errors += 0 if ok else 1
            lat.append((time.time() - t0) * 1000)
            time.sleep(0.2)
        if not lat:
            continue
        lat.sort()
        row = {"run_id": run_id, "timestamp": time.time(), "phase": phase, "request_count": len(lat),
               "errors": errors, "latency": {"p95": round(lat[int(0.95 * (len(lat) - 1))], 1)},
               "health": "ok" if errors == 0 else "degraded"}
        with open(out, "a", encoding="utf8") as f:
            f.write(json.dumps(row) + "\n")
        print(row)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], float(sys.argv[3]))
