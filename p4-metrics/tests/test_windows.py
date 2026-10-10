from datetime import datetime, timezone, timedelta
from p4_metrics.models import MetricRecord, LatencySummary
from p4_metrics.windows import MetricsWindow, separate_windows

def test_separate_windows():
    now = datetime.now(timezone.utc)
    base = MetricRecord(
        run_id="run-1", timestamp=now, phase="baseline", request_count=10, errors=0, latency=LatencySummary(), health="healthy"
    )
    fault = MetricRecord(
        run_id="run-1", timestamp=now + timedelta(seconds=1), phase="fault", request_count=10, errors=0, latency=LatencySummary(), health="healthy"
    )
    
    windows = separate_windows([base, fault])
    
    assert len(windows["baseline"].records) == 1
    assert len(windows["fault"].records) == 1
    
    # Recovery window should explicitly be marked as empty
    assert len(windows["recovery"].records) == 0
    assert windows["recovery"].is_empty is True
