from .models import MetricRecord

class MetricRecorder:
    """
    Appends MetricRecords to a JSONL file in real-time.
    """
    def __init__(self, file_path: str):
        self.file_path = file_path

    def record(self, metric: MetricRecord):
        # We append to the jsonl file synchronously for simplicity in this script,
        # but in a high-volume scenario, this could be batched.
        with open(self.file_path, 'a') as f:
            f.write(metric.model_dump_json() + '\n')
