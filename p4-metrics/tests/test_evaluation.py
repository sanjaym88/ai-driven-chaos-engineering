from datetime import datetime, timezone, timedelta
from p4_metrics.models import MetricRecord, LatencySummary
from p4_metrics.windows import separate_windows
from p4_metrics.evaluation import evaluate_run

def good_p3(run_id="r1", cleanup_verified=True):
    return {"run_id": run_id, "status": "completed", "stop_reason": "duration_elapsed", "cleanup_verified": cleanup_verified}

def test_successful_evaluation():
    now = datetime.now(timezone.utc)
    # 0% error rate
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(), health="healthy")
    
    # 50% error rate
    fault = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=10), phase="fault", request_count=100, errors=50, latency=LatencySummary(), health="degraded")
    
    # 1% error rate (Near baseline, indicates successful recovery)
    recovery = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=20), phase="recovery", request_count=100, errors=1, latency=LatencySummary(), health="healthy")
    
    windows = separate_windows([base, fault, recovery])
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"], good_p3())
    
    assert result.status == "valid"
    assert result.recovery_result == "success"

def test_incomplete_recovery():
    now = datetime.now(timezone.utc)
    base = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=100, errors=0, latency=LatencySummary(), health="healthy")
    fault = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=10), phase="fault", request_count=100, errors=50, latency=LatencySummary(), health="degraded")
    
    # High error rate remains in recovery phase
    recovery = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=20), phase="recovery", request_count=100, errors=45, latency=LatencySummary(), health="unhealthy")
    
    windows = separate_windows([base, fault, recovery])
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"], good_p3())
    
    assert result.status == "valid"
    assert result.recovery_result == "failed"

def test_missing_p3_result_fails_closed():
    now = datetime.now(timezone.utc)
    records = [
        MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=10, errors=0, latency=LatencySummary(values=[10]), health="healthy"),
        MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=1), phase="fault", request_count=10, errors=0, latency=LatencySummary(values=[20]), health="healthy"),
    ]
    windows = separate_windows(records)
    result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"])
    assert result.status == "invalid"
    assert result.cleanup_verified is False
    assert result.stop_reason == "Missing P3 result"

def test_mismatched_or_unverified_p3_result_fails_closed():
    now = datetime.now(timezone.utc)
    records = [
        MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=10, errors=0, latency=LatencySummary(values=[10]), health="healthy"),
        MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=1), phase="fault", request_count=10, errors=0, latency=LatencySummary(values=[20]), health="healthy"),
    ]
    windows = separate_windows(records)
    mismatch = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"], good_p3("other"))
    cleanup = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"], good_p3(cleanup_verified=False))
    assert mismatch.status == "invalid" and "mismatch" in mismatch.stop_reason
    assert cleanup.status == "invalid" and cleanup.cleanup_verified is False


def test_failed_or_blocked_p3_run_cannot_be_reported_as_valid():
    now = datetime.now(timezone.utc)
    records = [
        MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=10, errors=0, latency=LatencySummary(values=[10]), health="healthy"),
        MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=1), phase="fault", request_count=10, errors=0, latency=LatencySummary(values=[20]), health="healthy"),
    ]
    windows = separate_windows(records)
    for status in ("failed", "blocked"):
        p3 = good_p3()
        p3["status"] = status
        result = evaluate_run("r1", windows["baseline"], windows["fault"], windows["recovery"], p3)
        assert result.status == "invalid"
        assert "not completed" in result.stop_reason

def test_latency_spikes_classified_by_p95_severity():
    now = datetime.now(timezone.utc)
    baseline = MetricRecord(run_id="r1", timestamp=now, phase="baseline", request_count=4, errors=0, latency=LatencySummary(values=[10, 10, 10, 10]), health="healthy")
    doubled = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=1), phase="fault", request_count=4, errors=0, latency=LatencySummary(values=[21, 22, 23, 24]), health="healthy")
    critical = MetricRecord(run_id="r1", timestamp=now + timedelta(seconds=1), phase="fault", request_count=4, errors=0, latency=LatencySummary(values=[51, 52, 53, 54]), health="healthy")
    windows = separate_windows([baseline, doubled])
    from p4_metrics.evaluation import extract_failure_flags
    assert "LATENCY_DOUBLED" in extract_failure_flags(windows["baseline"], windows["fault"])
    critical_windows = separate_windows([baseline, critical])
    assert "LATENCY_CRITICAL_SPIKE" in extract_failure_flags(critical_windows["baseline"], critical_windows["fault"])
