from typing import List, Dict, Optional
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
    if base_lat > 0 and fault_lat > (base_lat * 5):
        flags.append("LATENCY_CRITICAL_SPIKE")
    elif base_lat > 0 and fault_lat > (base_lat * 2):
        flags.append("LATENCY_DOUBLED")
        
    # Health analysis
    if any(r.health == "unhealthy" for r in fault.records):
        flags.append("HEALTHCHECK_FAILED")
        
    return flags

def evaluate_run(run_id: str, baseline: MetricsWindow, fault: MetricsWindow, recovery: MetricsWindow, p3_result: Optional[Dict] = None) -> ResultEvaluation:
    """
    Performs deterministic evaluation summary for the entire run.
    """
    is_stale, stale_reason = check_stale_baseline(baseline, fault)
    
    if not p3_result:
        return ResultEvaluation(
            run_id=run_id,
            status="invalid",
            stop_reason="Missing P3 result",
            cleanup_verified=False,
            recovery_result="unknown",
            failure_flags=[]
        )
        
    if p3_result.get("run_id") != run_id:
        return ResultEvaluation(
            run_id=run_id,
            status="invalid",
            stop_reason=f"Run ID mismatch in P3 result: expected {run_id}, got {p3_result.get('run_id')}",
            cleanup_verified=False,
            recovery_result="unknown",
            failure_flags=[]
        )
        
    if "cleanup_verified" not in p3_result or "stop_reason" not in p3_result:
        return ResultEvaluation(
            run_id=run_id,
            status="invalid",
            stop_reason="Malformed P3 result: missing cleanup_verified or stop_reason",
            cleanup_verified=False,
            recovery_result="unknown",
            failure_flags=[]
        )
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
        base_lat = aggregate_latency(baseline)
        rec_lat = aggregate_latency(recovery)
        
        # If error rate and latency return to near-baseline levels, mark successful
        if rec_err <= base_err + 0.05 and rec_lat <= base_lat * 1.5:
            recovery_result = "success"
        else:
            recovery_result = "failed"
            
    failure_flags = extract_failure_flags(baseline, fault)
    
    # Consume P3's actual cleanup/result record if provided
    stop_reason = p3_result["stop_reason"]
    cleanup_verified = p3_result["cleanup_verified"]
            
    return ResultEvaluation(
        run_id=run_id,
        status="valid",
        stop_reason=stop_reason,
        cleanup_verified=cleanup_verified,  
        recovery_result=recovery_result,
        failure_flags=failure_flags
    )
