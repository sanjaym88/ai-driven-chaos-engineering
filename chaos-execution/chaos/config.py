"""Paths, safety config and small file helpers shared by all modules."""
import json
import os
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def runs_dir() -> Path:
    d = Path(os.environ.get("CHAOS_RUNS_DIR", ROOT / "runs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_dir(run_id: str) -> Path:
    d = runs_dir() / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_safety(path=None) -> dict:
    cfg = yaml.safe_load(Path(path or ROOT / "config" / "safety.yaml").read_text(encoding="utf8"))
    sb = cfg["sandbox"]
    sb["toxiproxy_url"] = os.environ.get("CHAOS_TOXIPROXY_URL", sb["toxiproxy_url"])
    sb["frontend_url"] = os.environ.get("CHAOS_FRONTEND_URL", sb["frontend_url"])
    metrics = Path(os.environ.get("CHAOS_METRICS_PATH", sb["metrics_path"]))
    sb["metrics_path"] = str(metrics if metrics.is_absolute() else ROOT / metrics)
    return cfg


def load_service_map(path=None) -> dict:
    return json.loads(Path(path or ROOT / "sandbox" / "service-map.json").read_text(encoding="utf8"))


def write_json(path, data) -> None:
    """Atomic write so the watchdog never reads a half-written file.

    On Windows, os.replace raises PermissionError while another process has the file open,
    so retry briefly instead of failing.
    """
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf8")
    for _ in range(100):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.02)
    # Still locked after ~2 s: write in place (readers tolerate a partial read and retry next poll).
    tmp.unlink(missing_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf8")


def read_json(path, default=None):
    for _ in range(50):
        try:
            return json.loads(Path(path).read_text(encoding="utf8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default
        except PermissionError:   # Windows: file is being replaced by a writer right now
            time.sleep(0.02)
    return default


def parse_ts(value) -> float:
    """Epoch seconds from an epoch number or an ISO-8601 string."""
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def now() -> float:
    return time.time()
