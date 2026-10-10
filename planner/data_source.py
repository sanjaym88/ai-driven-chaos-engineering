
"""Collect a measured baseline from the local Docker environment."""

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
OUTPUT_PATH = ROOT / "live-baseline.json"

FRONTEND = "http://localhost:9898"
BACKEND = "http://localhost:9899"
METRICS = "http://localhost:9797/metrics"
REQUEST_COUNT = 30
TIMEOUT_SECONDS = 3


def get_response(url, method="GET", body=None):
    """Return HTTP status, response body, and elapsed milliseconds."""
    request = Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    start = time.perf_counter()

    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            content = response.read().decode("utf-8", errors="replace")
            elapsed_ms = (time.perf_counter() - start) * 1000
            return response.status, content, elapsed_ms
    except HTTPError as exc:
        content = exc.read().decode("utf-8", errors="replace")
        elapsed_ms = (time.perf_counter() - start) * 1000
        return exc.code, content, elapsed_ms
    except (URLError, TimeoutError, OSError) as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def check_health(url):
    status, body, _ = get_response(url)
    return status == 200 and '"status":"OK"' in body.replace(" ", "")


def collect_baseline():
    print("Checking live services...")

    if not check_health(f"{FRONTEND}/healthz"):
        raise RuntimeError("Frontend health check failed.")

    if not check_health(f"{BACKEND}/healthz"):
        raise RuntimeError("Backend health check failed.")

    status, metrics_body, _ = get_response(METRICS)
    if status != 200 or "# TYPE http_requests_total counter" not in metrics_body:
        raise RuntimeError("Prometheus metrics endpoint is unavailable or unexpected.")

    print(f"Sending {REQUEST_COUNT} real requests through the frontend...")

    latencies = []
    errors = 0

    for index in range(REQUEST_COUNT):
        payload = json.dumps({
            "message": f"p2-baseline-{index}"
        }).encode("utf-8")

        try:
            status, response_body, elapsed_ms = get_response(
                f"{FRONTEND}/echo",
                method="POST",
                body=payload,
            )
            latencies.append(elapsed_ms)

            # A 200 response alone isn't enough if the app reports a
            # failed downstream call inside its response body.
            failed = (
                status < 200
                or status >= 300
                or "call failed" in response_body.lower()
                or "connection refused" in response_body.lower()
            )
            if failed:
                errors += 1

        except RuntimeError as exc:
            errors += 1
            print(f"Request {index + 1} failed: {exc}")

    if not latencies:
        raise RuntimeError("No requests completed; baseline not generated.")

    ordered = sorted(latencies)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    p95_ms = ordered[p95_index]
    error_rate = errors / REQUEST_COUNT

    frontend_healthy = check_health(f"{FRONTEND}/healthz")
    backend_healthy = check_health(f"{BACKEND}/healthz")
    health = (
        "healthy"
        if frontend_healthy and backend_healthy and error_rate == 0
        else "unhealthy"
    )

    baseline = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "source": "local-docker-live-requests",
        "sample_count": REQUEST_COUNT,
        "completed_requests": len(latencies),
        "errors": errors,
        "latency_p95_ms": round(p95_ms, 3),
        "error_rate": round(error_rate, 4),
        "health": health,
    }

    with OUTPUT_PATH.open("w", encoding="utf-8") as file:
        json.dump(baseline, file, indent=2)
        file.write("\n")

    print("\nLive baseline:")
    print(json.dumps(baseline, indent=2))
    print(f"\nSaved to: {OUTPUT_PATH}")

    if health != "healthy":
        print("WARNING: baseline is unhealthy; experiment planning should be blocked.")

    return baseline


if __name__ == "__main__":
    try:
        collect_baseline()
    except (RuntimeError, URLError, OSError) as exc:
        print(f"\nBaseline collection blocked: {exc}")
