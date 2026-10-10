import asyncio
import time
from datetime import datetime, timezone
import aiohttp
from typing import Callable, Dict, Optional
import statistics
from .models import MetricRecord, LatencySummary

class TrafficGenerator:
    """
    Generates steady HTTP traffic. Includes Addons for P1 (Health Checks) and P3 (Watchdog).
    """
    def __init__(self, target_url: str, health_url: Optional[str] = None, request_timeout: float = 2.0, max_concurrency: int = 10, stop_thresholds: Optional[Dict[str, float]] = None):
        self.target_url = target_url
        # ADDON FOR P1: Dedicated health check URL
        self.health_url = health_url or target_url 
        self.request_timeout = request_timeout
        self.semaphore = asyncio.Semaphore(max_concurrency)
        self.is_running = False
        self.stop_thresholds = stop_thresholds or {}

    async def _make_request(self, session: aiohttp.ClientSession, url: str) -> tuple[float, bool]:
        start = time.perf_counter()
        is_error = False
        try:
            async with self.semaphore:
                async with session.get(url, timeout=self.request_timeout) as response:
                    await response.read()
                    if response.status >= 400:
                        is_error = True
        except Exception:
            is_error = True
            
        latency = (time.perf_counter() - start) * 1000.0
        return latency, is_error

    def _check_safety_limits(self, latencies: list, error_rate: float, run_id: str):
        """ADDON FOR P3: FAST-TRIP WATCHDOG. Triggers a stop signal instantly on threshold breach."""
        if not self.stop_thresholds:
            return
            
        max_lat = self.stop_thresholds.get("max_latency_ms")
        max_err = self.stop_thresholds.get("max_error_rate")
        
        trip_reason = None
        if max_lat and latencies and statistics.mean(latencies) > max_lat:
            trip_reason = f"LATENCY_BREACH_{statistics.mean(latencies):.1f}ms"
        elif max_err and error_rate > max_err:
            trip_reason = f"ERROR_RATE_BREACH_{error_rate:.2f}"
            
        if trip_reason:
            # Emits a signal file that P3's watchdog can tail/watch for zero-latency shutdown
            with open(f"safety-trip-{run_id}.flag", "w") as f:
                f.write(trip_reason)
            print(f"⚠️ SAFETY TRIP TRIGGERED FOR P3: {trip_reason}")

    async def run(self, run_id: str, phase: str, duration_sec: int, record_callback: Callable[[MetricRecord], None]):
        self.is_running = True
        start_time = time.time()
        
        async with aiohttp.ClientSession() as session:
            while self.is_running and (time.time() - start_time) < duration_sec:
                window_start = datetime.now(timezone.utc)
                
                requests_per_second = 10
                tasks = [self._make_request(session, self.target_url) for _ in range(requests_per_second)]
                # Addon for P1: explicit health check poll
                health_task = self._make_request(session, self.health_url)
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                health_result = await health_task
                
                latencies = []
                errors = 0
                for r in results:
                    if isinstance(r, Exception):
                        errors += 1
                    else:
                        latency, is_error = r
                        latencies.append(latency)
                        if is_error:
                            errors += 1
                            
                # ADDON FOR P5: Percentile computation
                p50, p90, p99 = None, None, None
                if latencies:
                    s_lat = sorted(latencies)
                    p50 = s_lat[int(len(s_lat) * 0.5)]
                    p90 = s_lat[int(len(s_lat) * 0.9)]
                    p99 = s_lat[int(len(s_lat) * 0.99)]
                    
                is_health_error = health_result[1] if not isinstance(health_result, Exception) else True
                if is_health_error:
                    health_status = "unhealthy"
                else:
                    health_status = "healthy" if errors == 0 else "degraded"
                
                error_rate = errors / requests_per_second
                self._check_safety_limits(latencies, error_rate, run_id)
                
                record = MetricRecord(
                    run_id=run_id,
                    timestamp=window_start,
                    phase=phase,
                    request_count=len(tasks),
                    errors=errors,
                    latency=LatencySummary(unit="ms", values=latencies, p50=p50, p90=p90, p99=p99),
                    health=health_status
                )
                
                record_callback(record)
                await asyncio.sleep(1.0)
