"""Steady-rate traffic generator that records EVERY request.

Open-loop: one request is dispatched every 1/rate seconds regardless of how slow earlier ones are, so a
2-second fault does not throttle the sample rate. While running it also appends 1-second aggregate rows to
metrics.jsonl (phase, request_count, errors, latency, health) so the watchdog's live thresholds and the
runner's baseline preflight keep working on real measurements.
"""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .stats import percentile


class TrafficGenerator:
    def __init__(self, url: str, run_id: str, metrics_path, rate_hz: float = 10.0,
                 timeout_s: float = 6.0, max_workers: int = 64):
        self.url, self.run_id, self.metrics_path = url, run_id, Path(metrics_path)
        self.rate_hz, self.timeout_s = rate_hz, timeout_s
        self.phase = "baseline"                    # live label, used for the aggregate rows only
        self.records, self._lock = [], threading.Lock()
        self._stop = threading.Event()
        self._pool = ThreadPoolExecutor(max_workers=max_workers)
        self._threads = []
        self._reported = 0
        self.t0 = None

    def start(self):
        self.t0 = time.time()
        for target in (self._dispatch, self._aggregate):
            t = threading.Thread(target=target, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(5)
        self._pool.shutdown(wait=True)             # let in-flight requests finish (bounded by timeout_s)
        self._flush_aggregate()

    def snapshot(self) -> list:
        with self._lock:
            return list(self.records)

    def _dispatch(self):
        seq, nxt = 0, time.time()
        while not self._stop.is_set():
            self._pool.submit(self._one, seq, self.phase)
            seq += 1
            nxt += 1.0 / self.rate_hz
            time.sleep(max(0.0, nxt - time.time()))

    def _one(self, seq: int, phase: str):
        t_send = time.time()
        status, error, ok = None, None, False
        try:
            r = requests.post(self.url, json={"p3": "load", "seq": seq}, timeout=(3, self.timeout_s))
            status = r.status_code
            ok = status == 200 and "p3" in r.text      # echo returns the posted body (JSON-escaped)
            if not ok:
                error = f"http_{status}" if status != 200 else "bad_body"
        except requests.Timeout:
            error = "timeout"
        except requests.RequestException as e:
            error = type(e).__name__
        latency_ms = (time.time() - t_send) * 1000
        rec = {"run_id": self.run_id, "seq": seq, "phase": phase, "t_send": t_send,
               "t_rel": round(t_send - self.t0, 3), "latency_ms": round(latency_ms, 1),
               "status": status, "ok": ok, "error": error}
        with self._lock:
            self.records.append(rec)

    def _aggregate(self):
        while not self._stop.wait(1.0):
            self._flush_aggregate()

    def _flush_aggregate(self):
        with self._lock:
            new, self._reported = self.records[self._reported:], len(self.records)
        by_phase = {}
        for r in new:
            by_phase.setdefault(r["phase"], []).append(r)
        if not by_phase:
            return
        with open(self.metrics_path, "a", encoding="utf8") as f:
            for phase, rows in by_phase.items():
                lat = sorted(r["latency_ms"] for r in rows if r["ok"])
                errors = sum(1 for r in rows if not r["ok"])
                f.write(json.dumps({"run_id": self.run_id, "timestamp": time.time(), "phase": phase,
                                    "request_count": len(rows), "errors": errors,
                                    "latency": {"p95": percentile(lat, 95)},
                                    "health": "ok" if errors == 0 else "degraded"}) + "\n")
