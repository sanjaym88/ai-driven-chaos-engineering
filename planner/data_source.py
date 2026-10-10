"""Collect a live baseline through the verified P1 caller route."""

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
DEFAULT_SERVICE_MAP = ROOT.parent / "chaos-execution" / "sandbox" / "service-map.json"
OUTPUT_PATH = ROOT / "live-baseline.json"
REQUEST_COUNT = 30
TIMEOUT_SECONDS = 3


def get_response(url, method="GET", body=None):
    """Return HTTP status, response body, and elapsed milliseconds."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            content = response.read().decode("utf-8", errors="replace")
            return response.status, content, (time.perf_counter() - start) * 1000
    except HTTPError as exc:
        content = exc.read().decode("utf-8", errors="replace")
        return exc.code, content, (time.perf_counter() - start) * 1000
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def check_health(url):
    status, body, _ = get_response(url)
    return status == 200 and '"status":"OK"' in body.replace(" ", "")


def resolve_baseline_route(service_map):
    """Return the single verified caller route that P4 will measure."""
    if service_map.get("verification_status") != "verified":
        raise ValueError("P1 service map is not verified.")
    services = service_map.get("services")
    if not isinstance(services, list):
        raise ValueError("P1 service map services must be a list.")
    services_by_id = {item.get("id"): item for item in services if isinstance(item, dict)}
    edges = [edge for edge in service_map.get("dependencies", [])
             if isinstance(edge, dict) and edge.get("verification") == "passed"]
    if len(edges) != 1:
        raise ValueError("Baseline collection requires exactly one verified dependency route.")
    edge = edges[0]
    caller = services_by_id.get(edge.get("from"))
    route = edge.get("path")
    if not caller or not caller.get("host_url") or not isinstance(route, str):
        raise ValueError("Verified dependency route is missing caller host_url or path.")
    parts = route.split(None, 1)
    if len(parts) != 2:
        raise ValueError("Dependency path must include an HTTP method and path.")
    method, path = parts
    target_url = urljoin(caller["host_url"].rstrip("/") + "/", path.lstrip("/"))
    health_path = caller.get("health_path")
    if not isinstance(health_path, str) or not health_path.startswith("/"):
        raise ValueError("Caller service is missing an absolute health_path.")
    health_url = urljoin(caller["host_url"].rstrip("/") + "/", health_path.lstrip("/"))
    return target_url, health_url, method.upper()


def collect_baseline(service_map=None, output_path=OUTPUT_PATH):
    """Measure 30 real requests through the same mapped route P3 will fault."""
    if service_map is None:
        service_map = json.loads(DEFAULT_SERVICE_MAP.read_text(encoding="utf-8"))
    target_url, health_url, method = resolve_baseline_route(service_map)

    print(f"Checking caller health at {health_url}...")
    if not check_health(health_url):
        raise RuntimeError("Mapped caller health check failed.")

    print(f"Sending {REQUEST_COUNT} live requests through {method} {target_url}...")
    latencies = []
    errors = 0
    for index in range(REQUEST_COUNT):
        try:
            body = {"message": f"p2-baseline-{index}"} if method in {"POST", "PUT", "PATCH"} else None
            status, response_body, elapsed_ms = get_response(target_url, method=method, body=body)
            latencies.append(elapsed_ms)
            failed = status < 200 or status >= 300 or "call failed" in response_body.lower() or "connection refused" in response_body.lower()
            errors += int(failed)
        except RuntimeError as exc:
            errors += 1
            print(f"Request {index + 1} failed: {exc}")

    if not latencies:
        raise RuntimeError("No requests completed; baseline not generated.")

    ordered = sorted(latencies)
    p95_ms = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
    error_rate = errors / REQUEST_COUNT
    healthy = check_health(health_url)
    baseline = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "live-requests-through-p1-mapped-route",
        "request_method": method,
        "request_url": target_url,
        "sample_count": REQUEST_COUNT,
        "completed_requests": len(latencies),
        "errors": errors,
        "latency_p95_ms": round(p95_ms, 3),
        "error_rate": round(error_rate, 4),
        "health": "healthy" if healthy and error_rate == 0 else "unhealthy",
    }

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
    print("\nLive baseline:")
    print(json.dumps(baseline, indent=2))
    print(f"\nSaved to: {output_path}")
    if baseline["health"] != "healthy":
        print("WARNING: baseline is unhealthy; experiment planning will be blocked.")
    return baseline


if __name__ == "__main__":
    try:
        collect_baseline()
    except (RuntimeError, URLError, OSError, ValueError) as exc:
        raise SystemExit(f"\nBaseline collection blocked: {exc}")
