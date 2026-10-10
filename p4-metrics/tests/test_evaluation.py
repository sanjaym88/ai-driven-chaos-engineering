from datetime import datetime, timezone, timedelta
from p4_metrics.models import MetricRecord, LatencySummary
from p4_metrics.windows import separate_windows
from p4_metrics.evaluation import evaluate_run

def test_successful_evaluation():
    now = datetime.now(timezone.utc)
    # 0% error rate
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(), health="healthy")
    
    # 50% error rate
    fault = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=10), phase="fault", request_count=100, errors=50, latency=LatencySummary(), health="degraded")
    
    # 1% error rate (Near baseline, indicates successful recovery)
    recovery = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=20), phase="recovery", request_count=100, errors=1, latency=LatencySummary(), health="healthy")
    
    windows = separate_windows([base, fault, recovery])
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"])
    
    assert result.status == "valid"
    assert result.recovery_result == "success"

def test_incomplete_recovery():
    now = datetime.now(timezone.utc)
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(), health="healthy")
    fault = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=10), phase="fault", request_count=100, errors=50, latency=LatencySummary(), health="degraded")
    
    # High error rate remains in recovery phase
    recovery = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=20), phase="recovery", request_count=100, errors=45, latency=LatencySummary(), health="unhealthy")
    
    windows = separate_windows([base, fault, recovery])
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"])
    
    assert result.status == "valid"
    assert result.recovery_result == "failed"
