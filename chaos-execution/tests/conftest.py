"""A fake Toxiproxy API + fake frontend in one HTTP server, so every test runs without Docker."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

PROXY = "podinfo-backend"


class Fake(BaseHTTPRequestHandler):
    state = None

    def log_message(self, *a):
        pass

    def _send(self, code, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n).decode() if n else ""

    def do_GET(self):
        s = self.state
        if self.path == "/healthz":
            return self._send(200 if s["healthy"] else 503, {"status": "OK"})
        if self.path == f"/proxies/{PROXY}":
            return self._send(200, {"name": PROXY, "enabled": s["enabled"], "upstream": "backend:9898"})
        if self.path == f"/proxies/{PROXY}/toxics":
            return self._send(200, s["toxics"])
        self._send(404)

    def do_POST(self):
        s = self.state
        if self.path == "/echo":
            return self._send(200 if s["healthy"] else 502, json.loads(self._body() or "{}"))
        if self.path == f"/proxies/{PROXY}/toxics":
            toxic = json.loads(self._body())
            if any(t["name"] == toxic["name"] for t in s["toxics"]):
                return self._send(409)
            s["toxics"].append(toxic)
            return self._send(200, toxic)
        if self.path == f"/proxies/{PROXY}":
            s["enabled"] = json.loads(self._body())["enabled"]
            return self._send(200, {"name": PROXY, "enabled": s["enabled"]})
        self._send(404)

    def do_DELETE(self):
        s = self.state
        prefix = f"/proxies/{PROXY}/toxics/"
        if self.path.startswith(prefix):
            name = self.path[len(prefix):]
            if not any(t["name"] == name for t in s["toxics"]):
                return self._send(404)
            s["toxics"] = [t for t in s["toxics"] if t["name"] != name]
            return self._send(204)
        self._send(404)


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = {"toxics": [], "healthy": True, "enabled": True}
    handler = type("H", (Fake,), {"state": state})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    runs = tmp_path / "runs"
    monkeypatch.setenv("CHAOS_RUNS_DIR", str(runs))
    monkeypatch.setenv("CHAOS_TOXIPROXY_URL", url)
    monkeypatch.setenv("CHAOS_FRONTEND_URL", url)
    monkeypatch.setenv("CHAOS_METRICS_PATH", str(tmp_path / "metrics.jsonl"))
    yield state, tmp_path
    server.shutdown()


def write_metrics(tmp_path, rows):
    with open(tmp_path / "metrics.jsonl", "a", encoding="utf8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def good_baseline(run_id, requests_=30, age=5):
    return {"run_id": run_id, "timestamp": time.time() - age, "phase": "baseline",
            "request_count": requests_, "errors": 0, "latency": {"p95": 40}, "health": "ok"}


@pytest.fixture
def plan():
    return {"run_id": "t1", "candidate_id": "c1", "target": PROXY, "fault_type": "latency",
            "intensity": 300, "max_duration": 4,
            "stop_thresholds": {"error_rate": 0.2, "p95_latency_ms": 3000}, "reason_codes": ["TEST"]}
