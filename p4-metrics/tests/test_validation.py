import pytest
from datetime import datetime, timezone
from p4_metrics.models import MetricRecord, LatencySummary
from p4_metrics.validation import validate_run_id_consistency

def test_validate_run_id_consistency():
    record1 = MetricRecord(
        run_id="run-001",
        timestamp=datetime.now(timezone.utc),
        phase="baseline",
        request_count=10,
        errors=0,
        latency=LatencySummary(values=[10.0]),
        health="healthy"
    )
    record2 = MetricRecord(
        run_id="run-002",  # Mismatched run ID
        timestamp=datetime.now(timezone.utc),
        phase="baseline",
        request_count=10,
        errors=0,
        latency=LatencySummary(values=[10.0]),
        health="healthy"
    )
    
    # Validation should keep record1 and drop record2
    valid = validate_run_id_consistency([record1, record2], "run-001")
    assert len(valid) == 1
    assert valid[0].run_id == "run-001"

    # Validation should raise a ValueError if no records match
    with pytest.raises(ValueError):
        validate_run_id_consistency([record2], "run-001")
