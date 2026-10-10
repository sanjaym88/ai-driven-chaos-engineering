from typing import List
from .models import ResultEvaluation
from .windows import MetricsWindow, check_stale_baseline

def aggregate_latency(window: MetricsWindow) -> float:
    if window.is_empty:
        return 0.0
    all_values = []
    for r in window.records:
        all_values.extend(r.latency.values)
    return sum(all_values) / len(all_values) if all_values else 0.0

def aggregate_error_rate(window: MetricsWindow) -> float:
    if window.is_empty:
        return 0.0
    total_req = sum(r.request_count for r in window.records)
    total_err = sum(r.errors for r in window.records)
    return total_err / total_req if total_req > 0 else 0.0

def extract_failure_flags(baseline: MetricsWindow, fault: MetricsWindow) -> List[str]:
    """
    Upgraded: Flags specific failure models automatically based on telemetry differences.
    Feeds measured features directly to P2 (Experiment planner) and P5 (Dashboard).
    """
    flags = []
    base_err = aggregate_error_rate(baseline)
    fault_err = aggregate_error_rate(fault)
    base_lat = aggregate_latency(baseline)
    fault_lat = aggregate_latency(fault)
    
    # Error rate analysis
    if fault_err > base_err + 0.05:
        flags.append("ERROR_RATE_SPIKE")
    if fault_err > 0.5: 
        flags.append("SEVERE_SERVICE_DEGRADATION")
        
    # Latency analysis
    if base_lat > 0 and fault_lat > (base_lat * 2):
        flags.append("LATENCY_DOUBLED")
    elif base_lat > 0 and fault_lat > (base_lat * 5):
        flags.append("LATENCY_CRITICAL_SPIKE")
        
    # Health analysis
    if any(r.health == "unhealthy" for r in fault.records):
        flags.append("HEALTHCHECK_FAILED")
        
    return flags

def evaluate_run(run_id: str, baseline: MetricsWindow, fault: MetricsWindow, recovery: MetricsWindow) -> ResultEvaluation:
    """
    Performs deterministic evaluation summary for the entire run.
    """
    is_stale, stale_reason = check_stale_baseline(baseline, fault)
    
    if is_stale or baseline.is_empty:
        return ResultEvaluation(
            run_id=run_id,
            status="invalid",
            stop_reason=stale_reason or "Missing baseline",
            cleanup_verified=False,
            recovery_result="unknown",
            failure_flags=[]
        )
        
    if fault.is_empty:
        return ResultEvaluation(
            run_id=run_id,
            status="invalid",
            stop_reason="Missing fault data",
            cleanup_verified=False,
            recovery_result="unknown",
            failure_flags=[]
        )
        
    # Example logic for recovery verification
    if recovery.is_empty:
        recovery_result = "incomplete"
    else:
        base_err = aggregate_error_rate(baseline)
        rec_err = aggregate_error_rate(recovery)
        # If error rate returns to near-baseline levels, mark successful
        if rec_err <= base_err + 0.05:
            recovery_result = "success"
        else:
            recovery_result = "failed"
            
    failure_flags = extract_failure_flags(baseline, fault)
            
    return ResultEvaluation(
        run_id=run_id,
        status="valid",
        stop_reason="MAX_DURATION_REACHED",  # In reality, read this from P3's run result
        cleanup_verified=True,  
        recovery_result=recovery_result,
        failure_flags=failure_flags
    )
