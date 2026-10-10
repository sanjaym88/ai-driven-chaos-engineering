from typing import List, Dict, Tuple
from datetime import timedelta
from .models import MetricRecord

class MetricsWindow:
    def __init__(self, records: List[MetricRecord]):
        # Ensure records are sorted by timestamp
        self.records = sorted(records, key=lambda r: r.timestamp)
        
    @property
    def is_empty(self) -> bool:
        return len(self.records) == 0

    @property
    def start_time(self):
        return self.records[0].timestamp if not self.is_empty else None
        
    @property
    def end_time(self):
        return self.records[-1].timestamp if not self.is_empty else None

def separate_windows(records: List[MetricRecord]) -> Dict[str, MetricsWindow]:
    """
    Groups MetricRecords into baseline, fault, and recovery windows.
    """
    grouped = {"baseline": [], "fault": [], "recovery": []}
    for record in records:
        if record.phase in grouped:
            grouped[record.phase].append(record)
            
    return {
        "baseline": MetricsWindow(grouped["baseline"]),
        "fault": MetricsWindow(grouped["fault"]),
        "recovery": MetricsWindow(grouped["recovery"])
    }

def check_stale_baseline(baseline: MetricsWindow, fault: MetricsWindow, max_gap_seconds: int = 300) -> Tuple[bool, str]:
    """
    Validates if the baseline data is stale relative to the fault phase.
    """
    if baseline.is_empty:
        return True, "Baseline window is empty. No baseline data exists."
    if fault.is_empty:
        return False, "No fault data yet; baseline validity cannot be checked."
        
    gap = (fault.start_time - baseline.end_time).total_seconds()
    if gap > max_gap_seconds:
        return True, f"Baseline is stale (gap of {gap:.1f} seconds exceeds limit of {max_gap_seconds}s)."
        
    return False, "Baseline is valid."
