"""Read-only, loopback dashboard. No third-party dependencies or fault controls."""
import argparse
import json
import math
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parents[1]
STATIC = Path(__file__).resolve().parent / "static"
RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def reject_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def read_object(path, warnings):
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise ValueError("Expected an object")
        return value
    except (OSError, ValueError) as exc:
        warnings.append(f"Cannot read {path.name}: {exc}")
        return None


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def summarize(rows):
    result = {}
    for phase in ("baseline", "fault", "recovery"):
        selected = [r for r in rows if r.get("phase") == phase]
        values = sorted(v for r in selected for v in (r.get("latency") or {}).get("values", []) if finite(v) and v >= 0)
        count = sum(r.get("request_count", 0) for r in selected)
        errors = sum(r.get("errors", 0) for r in selected)
        def percentile(p):
            return round(values[max(0, math.ceil(len(values) * p) - 1)], 2) if values else None
        result[phase] = {"requests": count, "errors": errors,
                         "error_rate": errors / count if count else None,
                         "p50": percentile(.5), "p95": percentile(.95), "p99": percentile(.99),
                         "windows": len(selected)}
    return result


class Artifacts:
    def __init__(self, runs, metrics, service_map):
        self.runs = Path(runs).resolve()
        self.metrics = Path(metrics).resolve()
        self.service_map = Path(service_map).resolve()

    def directory(self, run_id):
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("Invalid run ID")
        path = (self.runs / run_id).resolve()
        if path.parent != self.runs:
            raise ValueError("Run is outside configured directory")
        return path

    def listing(self):
        runs, warnings = [], []
        if self.runs.exists():
            for path in self.runs.iterdir():
                if not path.is_dir() or not RUN_ID.fullmatch(path.name) or path.resolve().parent != self.runs:
                    continue
                state = read_object(path / "run.json", warnings)
                result = read_object(path / "result.json", warnings)
                if state is None and result is None:
                    continue
                plan = (result or {}).get("plan") or (state or {}).get("plan") or {}
                runs.append({"run_id": path.name, "status": (result or {}).get("status") or (state or {}).get("state", "unknown"),
                             "fault_type": plan.get("fault_type", "unknown"), "modified": path.stat().st_mtime})
        runs.sort(key=lambda r: r["modified"], reverse=True)
        return {"runs": runs, "warnings": warnings}

    def detail(self, run_id):
        path = self.directory(run_id)
        if not path.is_dir():
            raise FileNotFoundError("Run not found")
        warnings = []
        state = read_object(path / "run.json", warnings)
        p3 = read_object(path / "result.json", warnings)
        p4 = read_object(path / "p4-result.json", warnings)
        for name, data in (("P3", p3), ("P4", p4)):
            if data and data.get("run_id") != run_id:
                warnings.append(f"{name} run ID mismatch; result excluded")
                if name == "P3": p3 = None
                else: p4 = None
        rows = []
        try:
            with self.metrics.open(encoding="utf-8-sig") as stream:
                for line_num, line in enumerate(stream, 1):
                    if not line.strip(): continue
                    try:
                        row = json.loads(line, parse_constant=reject_constant)
                        if not isinstance(row, dict): raise ValueError("Expected an object")
                        if row.get("run_id") != run_id: continue
                        count, errors = row.get("request_count"), row.get("errors")
                        if not isinstance(count, int) or not isinstance(errors, int) or not 0 <= errors <= count:
                            raise ValueError("Invalid request counts")
                        lat = row.get("latency")
                        if not isinstance(lat, dict) or not isinstance(lat.get("values", []), list):
                            raise ValueError("Invalid latency samples")
                        rows.append(row)
                    except ValueError:
                        warnings.append(f"Unreadable metrics line {line_num}; retried on next refresh")
        except OSError:
            warnings.append("Metrics file unavailable; measurements are unknown")
        rows.sort(key=lambda r: str(r.get("timestamp", "")))
        plan = (p3 or {}).get("plan") or (state or {}).get("plan") or {}
        return {"run_id": run_id, "plan": plan, "state": state, "p3": p3, "p4": p4,
                "metrics": rows, "summary": summarize(rows), "warnings": warnings,
                "service_map": read_object(self.service_map, warnings),
                "evidence": [name for name in ("run.json", "result.json", "p4-result.json", "watchdog.json", "journal.json") if (path / name).is_file()]}


def handler(artifacts):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            try:
                if url.path == "/api/runs":
                    self.send_body(artifacts.listing())
                elif url.path == "/api/run":
                    run_id = parse_qs(url.query).get("id", [""])[0]
                    self.send_body(artifacts.detail(run_id))
                elif url.path in ("/", "/app.js", "/style.css"):
                    name = "index.html" if url.path == "/" else url.path[1:]
                    mime = {"index.html": "text/html", "app.js": "text/javascript", "style.css": "text/css"}[name]
                    self.send_body((STATIC / name).read_bytes(), mime)
                else:
                    self.send_body({"error": "Not found"}, status=404)
            except FileNotFoundError:
                self.send_body({"error": "Run not found"}, status=404)
            except ValueError as exc:
                self.send_body({"error": str(exc)}, status=400)
            except OSError:
                self.send_body({"error": "Artifacts temporarily unavailable"}, status=503)

        def send_body(self, data, mime="application/json", status=200):
            body = data if isinstance(data, bytes) else json.dumps(data, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", mime + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8085)
    parser.add_argument("--runs", default=ROOT / "chaos-execution/runs")
    parser.add_argument("--metrics", default=ROOT / "chaos-execution/runs/metrics.jsonl")
    parser.add_argument("--service-map", default=ROOT / "chaos-execution/sandbox/service-map.json")
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(Artifacts(args.runs, args.metrics, args.service_map)))
    print(f"Dashboard: http://127.0.0.1:{args.port} (read-only)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
