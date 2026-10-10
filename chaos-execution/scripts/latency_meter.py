"""Live latency meter for demos: one POST /echo per second, with a bar. Ctrl+C to stop."""
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chaos.config import load_safety  # noqa: E402

url = load_safety()["sandbox"]["frontend_url"].rstrip("/") + "/echo"
print(f"POST {url} every second (Ctrl+C to stop)")
while True:
    t0 = time.time()
    try:
        ok = requests.post(url, json={"meter": 1}, timeout=10).status_code == 200
    except requests.RequestException:
        ok = False
    ms = (time.time() - t0) * 1000
    print(f"{time.strftime('%H:%M:%S')}  {ms:7.0f} ms  {'#' * min(60, int(ms / 20))}{'' if ok else '  ERROR'}", flush=True)
    time.sleep(max(0, 1 - (time.time() - t0)))
