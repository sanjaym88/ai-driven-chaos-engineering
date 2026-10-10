from datetime import datetime, timezone, timedelta
from p4_metrics.models import MetricRecord, LatencySummary
from p4_metrics.windows import separate_windows, check_stale_baseline
from p4_metrics.evaluation import evaluate_run

def test_stale_baseline_rejected():
    now = datetime.now(timezone.utc)
    
    # Baseline recorded 10 minutes ago
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(), health="healthy")
    
    # Fault recorded now (> 300s gap)
    fault = MetricRecord(run_id="r1", timestamp=now + timedelta(minutes=10), phase="fault", request_count=100, errors=0, latency=LatencySummary(), health="healthy")
    
    windows = separate_windows([base, fault])
    is_stale, reason = check_stale_baseline(windows["baseline"], windows["fault"], max_gap_seconds=300)
    
    assert is_stale is True
    assert "exceeds limit" in reason
    
    # Evaluation should mark the entire run as invalid
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"])
    assert result.status == "invalid"
    assert "exceeds limit" in result.stop_reason

def test_missing_fault_data():
    now = datetime.now(timezone.utc)
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(), health="healthy")
    
    windows = separate_windows([base]) # Missing fault data
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"])
    
    assert result.status == "invalid"
    assert result.stop_reason == "Missing fault data"
