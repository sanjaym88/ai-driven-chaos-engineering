import asyncio
import math
import time
from datetime import datetime, timezone
import aiohttp
from typing import Any, Callable, Dict, Optional
from .models import MetricRecord, LatencySummary

class TrafficGenerator:
    """
    Generates steady HTTP traffic. Includes Addons for P1 (Health Checks) and P3 (Watchdog).
    """
    def __init__(self, target_url: str, health_url: Optional[str] = None, request_timeout: float = 2.0, max_concurrency: int = 10, stop_thresholds: Optional[Dict[str, float]] = None, request_method: str = "GET", request_body: Optional[Any] = None):
        self.target_url = target_url
        self.health_url = health_url or target_url 
        self.request_timeout = request_timeout
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.is_running = False
        self.stop_thresholds = stop_thresholds or {}
        self.request_method = request_method.upper()
        self.request_body = request_body
        self._safety_trip_written = False

    async def _make_request(self, session: aiohttp.ClientSession, url: str, method: str = "GET", body: Optional[Any] = None) -> tuple[float, bool, Optional[int]]:
        start = time.perf_counter()
        is_error = False
        status_code = None
        try:
            async with self.semaphore:
                async with session.request(method, url, json=body, timeout=self.request_timeout) as response:
                    await response.read()
                    status_code = response.status
                    if response.status >= 400:
                        is_error = True
        except Exception:
            is_error = True
            
        latency = (time.perf_counter() - start) * 1000.0
        return latency, is_error, status_code

    def _check_safety_limits(self, latencies: list, error_rate: float, run_id: str):
        """ADDON FOR P3: FAST-TRIP WATCHDOG. Triggers a stop signal instantly on threshold breach."""
        if not self.stop_thresholds:
            return
            
        max_lat = self.stop_thresholds.get("max_latency_p95_ms")
        if max_lat is None:
            max_lat = self.stop_thresholds.get("max_p95_latency_ms", self.stop_thresholds.get("max_latency_ms"))
        max_err = self.stop_thresholds.get("max_error_rate")
        
        trip_reasons = []
        if max_lat is not None and latencies:
            ordered = sorted(latencies)
            p95_lat = ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]
            if p95_lat > max_lat:
                trip_reasons.append(f"LATENCY_P95_BREACH_{p95_lat:.1f}ms")
        if max_err is not None and error_rate > max_err:
            trip_reasons.append(f"ERROR_RATE_BREACH_{error_rate:.2f}")
            
        if trip_reasons and not self._safety_trip_written:
            with open(f"safety-trip-{run_id}.flag", "w") as f:
                f.write(";".join(trip_reasons))
            self._safety_trip_written = True
            print(f"SAFETY TRIP SIGNAL FOR P3: {';'.join(trip_reasons)}")

    async def run(self, run_id: str, phase: str, duration_sec: int, record_callback: Callable[[MetricRecord], None]):
        self.is_running = True
        start = time.monotonic()
        stop_at = start + duration_sec
        request_rate = 10
        request_interval = 1.0 / request_rate
        next_request_at = start
        next_health_at = start
        next_window_at = start + 1.0
        window_opened_at = start
        window_start = datetime.now(timezone.utc)
        window_request_count = 0
        window_latencies = []
        window_errors = 0
        window_status_codes: Dict[str, int] = {}
        pending_requests = set()
        pending_health = set()
        last_health_error = None

        async with aiohttp.ClientSession() as session:
            while self.is_running and (time.monotonic() < stop_at or pending_requests or pending_health or window_opened_at < stop_at):
                now = time.monotonic()

                # Schedule requests at a fixed arrival rate instead of sending a burst
                # and then waiting for the slowest response before sending the next.
                if now < stop_at and now >= next_request_at:
                    pending_requests.add(asyncio.create_task(
                        self._make_request(session, self.target_url, self.request_method, self.request_body)
                    ))
                    next_request_at += request_interval
                    if next_request_at < now:
                        next_request_at = now + request_interval
                if now < stop_at and now >= next_health_at:
                    pending_health.add(asyncio.create_task(self._make_request(session, self.health_url)))
                    next_health_at = now + 1.0

                done_requests = {task for task in pending_requests if task.done()}
                for task in done_requests:
                    pending_requests.remove(task)
                    window_request_count += 1
                    try:
                        latency, is_error, status_code = task.result()
                    except Exception:
                        window_errors += 1
                        continue
                    window_latencies.append(latency)
                    if is_error:
                        window_errors += 1
                    if status_code is not None:
                        key = str(status_code)
                        window_status_codes[key] = window_status_codes.get(key, 0) + 1

                done_health = {task for task in pending_health if task.done()}
                for task in done_health:
                    pending_health.remove(task)
                    try:
                        _, last_health_error, _ = task.result()
                    except Exception:
                        last_health_error = True

                now = time.monotonic()
                if now >= next_window_at or (now >= stop_at and not pending_requests and not pending_health):
                    p50 = p95 = p99 = None
                    if window_latencies:
                        ordered = sorted(window_latencies)
                        p50 = ordered[max(0, math.ceil(len(ordered) * 0.50) - 1)]
                        p95 = ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]
                        p99 = ordered[max(0, math.ceil(len(ordered) * 0.99) - 1)]
                    if last_health_error is None:
                        health_status = "unknown"
                    elif last_health_error:
                        health_status = "unhealthy"
                    else:
                        health_status = "healthy" if window_errors == 0 else "degraded"
                    error_rate = window_errors / window_request_count if window_request_count else 0.0
                    window_duration = max(now - window_opened_at, 0.001)
                    self._check_safety_limits(window_latencies, error_rate, run_id)
                    record_callback(MetricRecord(
                        run_id=run_id,
                        timestamp=window_start,
                        phase=phase,
                        request_count=window_request_count,
                        errors=window_errors,
                        latency=LatencySummary(unit="ms", values=window_latencies, p50=p50, p95=p95, p99=p99),
                        health=health_status,
                        status_codes=window_status_codes,
                        throughput_rps=window_request_count / window_duration,
                        window_start=window_start,
                        window_end=datetime.now(timezone.utc),
                        request_method=self.request_method,
                        request_url=self.target_url
                    ))
                    window_latencies = []
                    window_errors = 0
                    window_request_count = 0
                    window_status_codes = {}
                    window_start = datetime.now(timezone.utc)
                    window_opened_at = now
                    while next_window_at <= now:
                        next_window_at += 1.0
                    if now >= stop_at and not pending_requests and not pending_health:
                        break

                # Poll frequently enough to keep both request arrival and window
                # boundaries close to their configured cadence.
                await asyncio.sleep(0.01)
