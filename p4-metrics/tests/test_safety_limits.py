from p4_metrics.traffic_generator import TrafficGenerator

def test_safety_limits():
    # Setup generator with both thresholds
    tg = TrafficGenerator("http://mock", stop_thresholds={
        "max_latency_ms": 100.0,
        "max_error_rate": 0.1
    })

    # 1. Both configured, neither breached
    tg._check_safety_limits(latencies=[50.0]*100, error_rate=0.0, run_id="r1")
    try:
        with open("safety-trip-r1.flag", "r") as f:
            assert False, "Should not have created flag"
    except FileNotFoundError:
        pass

    # 2. Both configured, only latency breached
    # p95 will be 150.0
    tg._check_safety_limits(latencies=[50.0]*90 + [150.0]*10, error_rate=0.0, run_id="r2")
    with open("safety-trip-r2.flag", "r") as f:
        content = f.read()
    assert "LATENCY_BREACH" in content
    assert "ERROR_RATE_BREACH" not in content

    # 3. Both configured, only error rate breached
    tg._check_safety_limits(latencies=[50.0]*100, error_rate=0.2, run_id="r3")
    with open("safety-trip-r3.flag", "r") as f:
        content = f.read()
    assert "LATENCY_BREACH" not in content
    assert "ERROR_RATE_BREACH" in content

    # 4. Both breached together
    tg._check_safety_limits(latencies=[50.0]*90 + [150.0]*10, error_rate=0.2, run_id="r4")
    with open("safety-trip-r4.flag", "r") as f:
        content = f.read()
    assert "LATENCY_BREACH" in content
    assert "ERROR_RATE_BREACH" in content
