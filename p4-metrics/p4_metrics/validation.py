import json
from typing import List
from .models import MetricRecord

def load_metrics_file(filepath: str) -> List[MetricRecord]:
    """Loads metrics.jsonl into MetricRecord objects."""
    records = []
    with open(filepath, 'r') as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                records.append(MetricRecord(**data))
            except Exception as e:
                raise ValueError(f"Malformed metric record at line {line_num}: {e}")
    return records

def validate_run_id_consistency(records: List[MetricRecord], expected_run_id: str) -> List[MetricRecord]:
    """
    Ensures all records belong to the exact same run_id.
    Filters out mismatched records and returns only valid ones, or raises an error depending on team policy.
    The README states: "A mismatched run_id is not merged into a run."
    """
    mismatched = sorted({record.run_id for record in records if record.run_id != expected_run_id})
    if mismatched:
        raise ValueError(
            f"Metrics contain run_id values other than '{expected_run_id}': {', '.join(mismatched)}"
        )
    if not records:
        raise ValueError("Metrics file contains no records.")
    return records
