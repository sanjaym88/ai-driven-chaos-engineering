import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from planner import data_source


class EchoHandler(BaseHTTPRequestHandler):
    requests_seen = 0

    def log_message(self, *args):
        pass

    def _send(self, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/healthz":
            return self._send({"status": "OK"})
        self.send_error(404)

    def do_POST(self):
        if self.path != "/echo":
            return self.send_error(404)
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size) or b"{}")
        type(self).requests_seen += 1
        return self._send(payload)


def test_live_baseline_uses_only_verified_caller_route(tmp_path, monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), EchoHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        service_map = json.loads(
            (Path(__file__).parents[1] / "fixtures" / "service-map.json").read_text(encoding="utf-8")
        )
        frontend = next(item for item in service_map["services"] if item["id"] == "podinfo-frontend")
        frontend["host_url"] = f"http://127.0.0.1:{server.server_address[1]}"
        monkeypatch.setattr(data_source, "REQUEST_COUNT", 3)

        baseline = data_source.collect_baseline(service_map, tmp_path / "baseline.json")

        assert baseline["health"] == "healthy"
        assert baseline["sample_count"] == 3
        assert baseline["completed_requests"] == 3
        assert baseline["request_method"] == "POST"
        assert baseline["request_url"].endswith("/echo")
        assert EchoHandler.requests_seen == 3
        assert (tmp_path / "baseline.json").exists()
    finally:
        server.shutdown()
