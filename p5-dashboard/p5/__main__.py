"""P5 command line.

  python -m p5 validate [RUNS_DIR]     check the team's files against contracts/ (do this at merge!)
  python -m p5 report [RUN_ID|--all]   write out/reports/<run>.html + .md
  python -m p5 worker [--once]         Slack notifier (dry-run unless SLACK_WEBHOOK_URL is set)
  python -m p5 outbox                  show queued / sent / dead notifications
  streamlit run app.py                 the dashboard
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

from . import config
from .adapters import load_run, load_runs
from .notify import Outbox, Worker, default_sender
from .report import write_reports

CONTRACTS = config.ROOT / "contracts"


def _schema(name):
    return Draft202012Validator(json.loads((CONTRACTS / name).read_text()))


def validate(runs_dir: Path, map_path: Path) -> int:
    """Strict check of raw files against the contracts. Returns number of problems."""
    problems = 0
    checks = {"result.json": _schema("result.schema.json"), "plan.json": _schema("plan.schema.json")}
    line_schema = _schema("metrics-line.schema.json")
    if map_path.exists():
        for e in _schema("service-map.schema.json").iter_errors(json.loads(map_path.read_text())):
            problems += 1
            print(f"  {map_path.name}: {e.json_path} {e.message}")
    else:
        problems += 1
        print(f"  service map not found at {map_path}")
    for d in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        for fname, v in checks.items():
            f = d / fname
            if not f.exists():
                problems += 1
                print(f"  {d.name}/{fname}: missing")
                continue
            for e in v.iter_errors(json.loads(f.read_text())):
                problems += 1
                print(f"  {d.name}/{fname}: {e.json_path} {e.message}")
        m = d / "metrics.jsonl"
        if m.exists():
            for n, text in enumerate(m.read_text().splitlines(), 1):
                if text.strip():
                    for e in line_schema.iter_errors(json.loads(text)):
                        problems += 1
                        print(f"  {d.name}/metrics.jsonl line {n}: {e.json_path} {e.message}")
                        break
        run = load_run(d)
        for p in run.problems:
            print(f"  {d.name}: {p}")
    print("OK - files match the contracts." if not problems else f"{problems} contract problem(s).")
    return problems


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "help"
    if cmd == "validate":
        rd = Path(argv[1]) if len(argv) > 1 else config.runs_dir()
        return 1 if validate(rd, config.map_path()) else 0
    if cmd == "report":
        runs = load_runs(config.runs_dir())
        if len(argv) > 1 and argv[1] != "--all":
            runs = [r for r in runs if r.run_id == argv[1]]
        for r in runs:
            print("  wrote", *write_reports(r))
        return 0
    if cmd == "worker":
        w = Worker(Outbox(), default_sender())
        if "--once" in argv:
            print(f"  queued {w.scan(config.runs_dir())}, delivery {w.deliver()}")
        else:
            w.run_forever(config.runs_dir())
        return 0
    if cmd == "outbox":
        for e in Outbox().all():
            print(f"  {e['event_id']:24s} {e['status']:8s} attempts={e['attempts']} {e['last_error'] or ''}")
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
