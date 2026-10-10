from datetime import datetime, timezone, timedelta
from p4_metrics.models import MetricRecord, LatencySummary
from p4_metrics.windows import separate_windows
from p4_metrics.evaluation import evaluate_run, extract_failure_flags

def test_successful_evaluation():
    now = datetime.now(timezone.utc)
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(values=[10.0]*100), health="healthy")
    fault = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=10), phase="fault", request_count=100, errors=50, latency=LatencySummary(values=[10.0]*100), health="degraded")
    recovery = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=20), phase="recovery", request_count=100, errors=1, latency=LatencySummary(values=[10.0]*100), health="healthy")
    
    windows = separate_windows([base, fault, recovery])
    
    p3_result = {"run_id": "r1", "stop_reason": "MAX_DURATION_REACHED", "cleanup_verified": True}
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"], p3_result)
    
    assert result.status == "valid"
    assert result.recovery_result == "success"

def test_incomplete_recovery():
    now = datetime.now(timezone.utc)
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(values=[10.0]*100), health="healthy")
    fault = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=10), phase="fault", request_count=100, errors=50, latency=LatencySummary(values=[10.0]*100), health="degraded")
    recovery = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=20), phase="recovery", request_count=100, errors=45, latency=LatencySummary(values=[10.0]*100), health="unhealthy")
    
    windows = separate_windows([base, fault, recovery])
    p3_result = {"run_id": "r1", "stop_reason": "MAX_DURATION_REACHED", "cleanup_verified": True}
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"], p3_result)
    
    assert result.status == "valid"
    assert result.recovery_result == "failed"

def test_missing_p3_result():
    now = datetime.now(timezone.utc)
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(values=[10.0]*100), health="healthy")
    fault = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=10), phase="fault", request_count=100, errors=0, latency=LatencySummary(values=[10.0]*100), health="healthy")
    
    windows = separate_windows([base, fault])
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["baseline"], None)
    
    assert result.status == "invalid"
    assert result.stop_reason == "Missing P3 result"

def test_mismatched_p3_result_run_id():
    now = datetime.now(timezone.utc)
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(values=[10.0]*100), health="healthy")
    fault = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=10), phase="fault", request_count=100, errors=0, latency=LatencySummary(values=[10.0]*100), health="healthy")
    
    windows = separate_windows([base, fault])
    p3_result = {"run_id": "r2", "stop_reason": "MAX_DURATION", "cleanup_verified": True}
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["baseline"], p3_result)
    
    assert result.status == "invalid"
    assert "Run ID mismatch" in result.stop_reason

def test_malformed_p3_result():
    now = datetime.now(timezone.utc)
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(values=[10.0]*100), health="healthy")
    fault = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=10), phase="fault", request_count=100, errors=0, latency=LatencySummary(values=[10.0]*100), health="healthy")
    
    windows = separate_windows([base, fault])
    p3_result = {"run_id": "r1"}  # Missing stop_reason and cleanup_verified
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["baseline"], p3_result)
    
    assert result.status == "invalid"
    assert "Malformed P3 result" in result.stop_reason

def test_cleanup_false_p3_result():
    now = datetime.now(timezone.utc)
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(values=[10.0]*100), health="healthy")
    fault = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=10), phase="fault", request_count=100, errors=0, latency=LatencySummary(values=[10.0]*100), health="healthy")
    
    windows = separate_windows([base, fault])
    p3_result = {"run_id": "r1", "stop_reason": "MAX_DURATION", "cleanup_verified": False}
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["baseline"], p3_result)
    
    assert result.status == "valid"
    assert result.cleanup_verified is False

def test_failure_classification_latency():
    now = datetime.now(timezone.utc)
    # Baseline latency: 10ms
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=1, errors=0, latency=LatencySummary(values=[10.0]), health="healthy")
    
    # Fault latency: 25ms (> 2x baseline)
    fault_2x = MetricRecord(run_id="r1", timestamp=now, phase="fault", request_count=1, errors=0, latency=LatencySummary(values=[25.0]), health="healthy")
    windows_2x = separate_windows([base, fault_2x])
    flags_2x = extract_failure_flags(windows_2x["baseline"], windows_2x["fault"])
    assert "LATENCY_DOUBLED" in flags_2x
    assert "LATENCY_CRITICAL_SPIKE" not in flags_2x
    
    # Fault latency: 55ms (> 5x baseline)
    fault_5x = MetricRecord(run_id="r1", timestamp=now, phase="fault", request_count=1, errors=0, latency=LatencySummary(values=[55.0]), health="healthy")
    windows_5x = separate_windows([base, fault_5x])
    flags_5x = extract_failure_flags(windows_5x["baseline"], windows_5x["fault"])
    assert "LATENCY_CRITICAL_SPIKE" in flags_5x
    assert "LATENCY_DOUBLED" not in flags_5x
