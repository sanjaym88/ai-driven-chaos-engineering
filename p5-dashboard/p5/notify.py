"""Slack notifications through an outbox - delivery can NEVER block or delay fault cleanup.

How it works
  1. The worker scans the runs folder. Every run whose result.json has a final status
     gets ONE event, id = "<run_id>:<status>". Inserting the same id twice does nothing
     (dedup). P3 does not have to call anything - writing result.json is enough.
  2. The worker sends due events to Slack. On failure it retries with exponential
     backoff; after MAX_ATTEMPTS it marks the event "dead" and moves on.
  3. No webhook configured -> dry-run: messages are appended to out/slack_dry_run.log.

The outbox is a small SQLite file (out/outbox.db). Everything here runs in its own
process (`python -m p5 worker`), separate from the experiment runner.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import requests

from . import config
from .adapters import Run, load_runs
from .facts import build_facts, fmt
from .findings import evaluate

MAX_ATTEMPTS = 6
BASE_BACKOFF_S = 2.0
EMOJI = {"completed": ":white_check_mark:", "aborted": ":octagonal_sign:",
         "failed": ":rotating_light:", "blocked": ":no_entry:"}


# ------------------------------------------------------------------ message templates (fixed)
def message_for(run: Run) -> dict:
    f = build_facts(run)
    s = f["status"]
    link = f"{config.report_base_url()}/?run={f['run_id']}"
    what = f"{f['fault_type']} ({f['intensity']}) on `{f['target']}`"
    fault, base = f["phases"]["fault"], f["phases"]["baseline"]
    rec = (f"recovered in {fmt(f['recovery_time_s'], 1)}" if f["recovery_time_s"].known
           else f"recovery: {f['recovery_status']}")
    if s == "blocked":
        lines = [f"{EMOJI[s]} *{f['run_id']} BLOCKED* before injection - {what}",
                 f"Reason: {f['stop_reason']}. *No fault was injected.*"]
    else:
        head = {"completed": "completed", "aborted": f"ABORTED by {f['stopped_by'] or 'safety system'}",
                "failed": "FAILED - needs attention"}.get(s, s)
        lines = [f"{'<!here> ' if s == 'failed' else ''}{EMOJI.get(s, '')} *{f['run_id']} {head}* - {what}",
                 f"p95 {fmt(base['p95_ms'], 1)} -> {fmt(fault['p95_ms'], 1)} · "
                 f"errors {fmt(base['error_rate'])} -> {fmt(fault['error_rate'])} · {rec} · "
                 f"cleanup verified: {fmt(f['cleanup_verified'])}",
                 f"Stop reason: {f['stop_reason']}"]
        if s == "failed" and f["failure_detail"]:
            lines.append(f"Detail: {f['failure_detail']}")
        top = [x for x in evaluate(f) if x.severity in ("critical", "high")][:2]
        lines += [f"• [{x.severity}] {x.title}" for x in top]
    lines.append(f"Report: {link}")
    return {"text": "\n".join(lines), "urgent": s == "failed"}


# ------------------------------------------------------------------ outbox
SCHEMA = """CREATE TABLE IF NOT EXISTS events(
  event_id TEXT PRIMARY KEY, run_id TEXT, kind TEXT, payload TEXT,
  status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0, next_attempt_at REAL DEFAULT 0,
  last_error TEXT, created_at REAL, sent_at REAL)"""


class Outbox:
    def __init__(self, path: Path | None = None):
        self.path = path or (config.out_dir() / "outbox.db")
        self.db = sqlite3.connect(self.path, timeout=5)
        self.db.execute(SCHEMA)
        self.db.commit()

    def enqueue(self, event_id: str, run_id: str, kind: str, payload: dict) -> bool:
        """Returns True if new, False if this event id already exists (deduplicated)."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO events(event_id, run_id, kind, payload, created_at) VALUES (?,?,?,?,?)",
            (event_id, run_id, kind, json.dumps(payload), time.time()))
        self.db.commit()
        return cur.rowcount == 1

    def exists(self, event_id: str) -> bool:
        return self.db.execute("SELECT 1 FROM events WHERE event_id=?", (event_id,)).fetchone() is not None

    def due(self, now: float) -> list[tuple]:
        return self.db.execute(
            "SELECT event_id, payload, attempts FROM events WHERE status='pending' AND next_attempt_at<=? "
            "ORDER BY created_at", (now,)).fetchall()

    def mark_sent(self, event_id: str) -> None:
        self.db.execute("UPDATE events SET status='sent', sent_at=?, attempts=attempts+1, last_error=NULL "
                        "WHERE event_id=?", (time.time(), event_id))
        self.db.commit()

    def mark_failed(self, event_id: str, attempts: int, error: str, now: float) -> None:
        attempts += 1
        status = "dead" if attempts >= MAX_ATTEMPTS else "pending"
        nxt = now + BASE_BACKOFF_S * 2 ** (attempts - 1)
        self.db.execute("UPDATE events SET status=?, attempts=?, next_attempt_at=?, last_error=? WHERE event_id=?",
                        (status, attempts, nxt, error[:300], event_id))
        self.db.commit()

    def all(self) -> list[dict]:
        cols = ["event_id", "run_id", "kind", "status", "attempts", "last_error", "created_at", "sent_at"]
        rows = self.db.execute(f"SELECT {', '.join(cols)} FROM events ORDER BY created_at DESC").fetchall()
        return [dict(zip(cols, r)) for r in rows]


# ------------------------------------------------------------------ senders
class Sender(Protocol):
    def __call__(self, payload: dict) -> None: ...   # raise on failure


def slack_sender(webhook: str) -> Sender:
    def send(payload: dict) -> None:
        r = requests.post(webhook, json={"text": payload["text"]}, timeout=5)
        if r.status_code >= 300:
            raise RuntimeError(f"Slack HTTP {r.status_code}: {r.text[:100]}")
    return send


def dry_run_sender(log: Path | None = None) -> Sender:
    log = log or (config.out_dir() / "slack_dry_run.log")

    def send(payload: dict) -> None:
        with open(log, "a") as fh:
            fh.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')}{' URGENT' if payload.get('urgent') else ''}\n"
                     f"{payload['text']}\n")
    return send


def default_sender() -> Sender:
    hook = config.slack_webhook()
    return slack_sender(hook) if hook else dry_run_sender()


# ------------------------------------------------------------------ worker
@dataclass
class Worker:
    outbox: Outbox
    send: Sender
    clock: Callable[[], float] = time.time

    def scan(self, runs_dir: Path) -> int:
        """Queue one event per final run. Safe to call repeatedly."""
        new = 0
        for run in load_runs(runs_dir):
            eid = f"{run.run_id}:{run.status}"
            if run.final and not self.outbox.exists(eid):
                new += self.outbox.enqueue(eid, run.run_id, run.status, message_for(run))
        return new

    def deliver(self) -> dict:
        stats = {"sent": 0, "failed": 0}
        now = self.clock()
        for event_id, payload, attempts in self.outbox.due(now):
            try:
                self.send(json.loads(payload))
                self.outbox.mark_sent(event_id)
                stats["sent"] += 1
            except Exception as e:  # any Slack/network error: retry later, never crash the loop
                self.outbox.mark_failed(event_id, attempts, f"{type(e).__name__}: {e}", now)
                stats["failed"] += 1
        return stats

    def run_forever(self, runs_dir: Path, interval_s: float = 3.0) -> None:
        print(f"P5 notifier: watching {runs_dir} -> {'Slack' if config.slack_webhook() else 'dry-run log'}")
        while True:
            q = self.scan(runs_dir)
            d = self.deliver()
            if q or d["sent"] or d["failed"]:
                print(f"  queued {q}, sent {d['sent']}, failed {d['failed']}")
            time.sleep(interval_s)
