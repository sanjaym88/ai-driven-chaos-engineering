"""P5 evaluations from the team plan, runnable alone:   pytest -q

- every status renders (completed / aborted / failed / blocked)
- report numbers exactly match the source records (independent recalculation)
- missing data shows "unknown", never a guess, never a crash
- same input -> same report (deterministic)
- Slack: failure + retry -> exactly one message; duplicates dropped; dead after max attempts
- Slack can never block: scanning never touches the network, send errors never escape
"""
from __future__ import annotations

import json
import shutil
import statistics
from pathlib import Path

import pytest

from p5 import config
from p5.__main__ import validate
from p5.adapters import load_run, load_runs
from p5.facts import build_facts, fmt
from p5.findings import evaluate
from p5.notify import MAX_ATTEMPTS, Outbox, Worker, message_for
from p5.report import render_html, render_md

FIX = config.ROOT / "fixtures" / "runs"
RUN_IDS = ["run-0001", "run-0002", "run-0003", "run-0004"]


@pytest.fixture
def runs_copy(tmp_path) -> Path:
    dst = tmp_path / "runs"
    shutil.copytree(FIX, dst)
    return dst


# ------------------------------------------------------------------ contracts
def test_fixtures_match_contracts(capsys):
    assert validate(FIX, config.ROOT / "fixtures" / "service-map.json") == 0


# ------------------------------------------------------------------ rendering
@pytest.mark.parametrize("rid,status", list(zip(RUN_IDS, ["completed", "aborted", "failed", "blocked"])))
def test_each_status_renders(rid, status):
    run = load_run(FIX / rid)
    html, md = render_html(run), render_md(run)
    assert run.status == status
    assert status in html and status.upper() in md
    for text in (html, md):
        assert "None" not in text and "nan" not in text.lower().replace("nanoseconds", "")


def test_blocked_run_says_nothing_injected():
    md = render_md(load_run(FIX / "run-0004"))
    assert "No fault was injected" in md
    assert "F0_BLOCKED" in md


def test_failed_run_flags_cleanup_and_recovery():
    ids = [f.rule_id for f in evaluate(build_facts(load_run(FIX / "run-0003")))]
    assert ids[:2] == ["F1_CLEANUP_UNVERIFIED", "F2_NOT_RECOVERED"]


# ------------------------------------------------------------------ numbers match records
def _raw_phase(path: Path, phase: str) -> dict:
    """Independent re-calculation straight from the raw JSON lines (no p5 code)."""
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    rows = [r for r in rows if r["phase"] == phase]
    if not rows:
        return {}
    req = sum(r["request_count"] for r in rows)
    err = sum(r["errors"] for r in rows)
    return {"requests": req, "errors": err, "error_rate": err / req,
            "p95_ms": round(max(r["latency"]["p95_ms"] for r in rows), 1),
            "p50_ms": round(statistics.median(r["latency"]["p50_ms"] for r in rows), 1)}


@pytest.mark.parametrize("rid", RUN_IDS)
def test_report_numbers_match_source_records(rid):
    run = load_run(FIX / rid)
    facts = build_facts(run)
    md = render_md(run)
    for phase in ("baseline", "fault", "recovery"):
        raw = _raw_phase(FIX / rid / "metrics.jsonl", phase)
        got = facts["phases"][phase]
        if not raw:
            assert not got["p95_ms"].known
            continue
        for key, val in raw.items():
            assert got[key].value == pytest.approx(val), f"{rid} {phase} {key}"
        # the rendered table shows exactly those values
        for key in ("requests", "errors", "error_rate"):
            assert fmt(got[key]) in md
        assert fmt(got["p95_ms"], 1) in md
    res = json.loads((FIX / rid / "result.json").read_text())
    rt = (res.get("recovery_result") or {}).get("recovery_time_s")
    if rt is not None:
        assert f"{rt:.1f} s" in md


def test_same_input_same_report():
    run = load_run(FIX / "run-0002")
    assert render_html(run) == render_html(load_run(FIX / "run-0002"))
    assert render_md(run) == render_md(load_run(FIX / "run-0002"))


# ------------------------------------------------------------------ unknown stays unknown
def test_missing_metrics_and_recovery_show_unknown(runs_copy):
    d = runs_copy / "run-0001"
    (d / "metrics.jsonl").unlink()
    res = json.loads((d / "result.json").read_text())
    res["recovery_result"]["recovery_time_s"] = None
    res["cleanup_verified"] = None
    (d / "result.json").write_text(json.dumps(res))
    run = load_run(d)
    md = render_md(run)
    assert "metrics.jsonl missing" in run.problems
    assert "| Requests | unknown | unknown | unknown |" in md
    assert "**Cleanup verified:** unknown" in md
    assert any(f.severity == "unknown" for f in evaluate(build_facts(run)))


def test_corrupt_files_do_not_crash(runs_copy):
    d = runs_copy / "run-0002"
    (d / "result.json").write_text("{ not json")
    with open(d / "metrics.jsonl", "a") as fh:
        fh.write("garbage line\n")
    run = load_run(d)
    assert run.status is None
    assert any("not valid JSON" in p for p in run.problems)
    assert any("line" in p and "skipped" in p for p in run.problems)
    assert "unknown" in render_md(run)


def test_plain_number_latency_is_accepted(runs_copy):
    d = runs_copy / "run-0001"
    lines = [json.loads(x) for x in (d / "metrics.jsonl").read_text().splitlines()]
    for x in lines:
        x["latency"] = x["latency"]["p95_ms"]
    (d / "metrics.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    f = build_facts(load_run(d))
    assert f["phases"]["fault"]["p95_ms"].known and not f["phases"]["fault"]["p50_ms"].known


# ------------------------------------------------------------------ Slack outbox
class FlakySender:
    def __init__(self, fail_times: int):
        self.fail_times, self.calls, self.delivered = fail_times, 0, []

    def __call__(self, payload):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("Slack unavailable")
        self.delivered.append(payload["text"])


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_slack_failure_retries_then_exactly_one_message(tmp_path, runs_copy):
    sender, clock = FlakySender(fail_times=2), Clock()
    w = Worker(Outbox(tmp_path / "o.db"), sender, clock)
    shutil.rmtree(runs_copy / "run-0002"); shutil.rmtree(runs_copy / "run-0003"); shutil.rmtree(runs_copy / "run-0004")
    assert w.scan(runs_copy) == 1
    for _ in range(6):
        w.deliver()
        clock.t += 100          # jump past any backoff
    assert len(sender.delivered) == 1 and sender.calls == 3
    assert w.outbox.all()[0]["status"] == "sent"


def test_duplicate_events_are_dropped(tmp_path, runs_copy):
    ob = Outbox(tmp_path / "o.db")
    assert ob.enqueue("run-0001:completed", "run-0001", "completed", {"text": "x"}) is True
    assert ob.enqueue("run-0001:completed", "run-0001", "completed", {"text": "x"}) is False
    w = Worker(ob, FlakySender(0), Clock())
    w.scan(runs_copy); w.scan(runs_copy)
    assert len(ob.all()) == 4


def test_dead_after_max_attempts_and_loop_survives(tmp_path, runs_copy):
    clock = Clock()
    w = Worker(Outbox(tmp_path / "o.db"), FlakySender(fail_times=10_000), clock)
    w.scan(runs_copy)
    for _ in range(MAX_ATTEMPTS + 2):
        w.deliver()                 # must never raise
        clock.t += 10_000
    assert {e["status"] for e in w.outbox.all()} == {"dead"}


def test_scanning_never_touches_the_network(tmp_path, runs_copy, monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network!")))
    w = Worker(Outbox(tmp_path / "o.db"), FlakySender(0), Clock())
    assert w.scan(runs_copy) == 4      # queueing is local only


def test_failed_run_message_is_urgent_and_from_records():
    msg = message_for(load_run(FIX / "run-0003"))
    assert msg["urgent"] and "<!here>" in msg["text"] and "67.0%" in msg["text"]
    blocked = message_for(load_run(FIX / "run-0004"))
    assert "No fault was injected" in blocked["text"] and not blocked["urgent"]


def test_runs_sorted_newest_first():
    assert [r.run_id for r in load_runs(FIX)] == ["run-0004", "run-0003", "run-0002", "run-0001"]
