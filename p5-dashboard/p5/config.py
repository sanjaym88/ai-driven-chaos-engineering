"""All P5 settings come from environment variables, so merging is just pointing these at the team's files.

  P5_RUNS_DIR          folder with one sub-folder per run   (default: fixtures/runs)
  P5_MAP_PATH          service-map.json                     (default: fixtures/service-map.json)
  P5_OUT_DIR           where reports + outbox are written   (default: out)
  P5_REPORT_BASE_URL   link used in Slack messages          (default: http://localhost:8501)
  SLACK_WEBHOOK_URL    Slack incoming webhook; unset = dry-run (messages go to out/slack_dry_run.log)
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def runs_dir() -> Path:
    return Path(os.getenv("P5_RUNS_DIR", ROOT / "fixtures" / "runs"))


def map_path() -> Path:
    return Path(os.getenv("P5_MAP_PATH", ROOT / "fixtures" / "service-map.json"))


def out_dir() -> Path:
    p = Path(os.getenv("P5_OUT_DIR", ROOT / "out"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def report_base_url() -> str:
    return os.getenv("P5_REPORT_BASE_URL", "http://localhost:8501").rstrip("/")


def slack_webhook() -> str | None:
    return os.getenv("SLACK_WEBHOOK_URL") or None
