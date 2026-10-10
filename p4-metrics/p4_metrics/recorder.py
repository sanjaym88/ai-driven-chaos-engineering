from .models import MetricRecord
from pathlib import Path

class MetricRecorder:
    """
    Appends MetricRecords to a JSONL file in real-time.
    """
    def __init__(self, file_path: str):
        self.file_path = file_path

    def record(self, metric: MetricRecord):
        # We append to the jsonl file synchronously for simplicity in this script,
        # but in a high-volume scenario, this could be batched.
        path = Path(self.file_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(metric.model_dump_json() + '\n')
