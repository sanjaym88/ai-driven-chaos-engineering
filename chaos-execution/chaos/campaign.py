"""Fault campaign: many single-fault cases, run strictly one at a time.

  python -m chaos.campaign --repeats 7                 # 22 fault configs x 7 = 154 cases
  python -m chaos.campaign --limit 3 --id smoke        # quick check
  python -m chaos.campaign --id c1 --resume            # continue an interrupted campaign
  python -m chaos.campaign --id c1 --finalize-only     # rebuild summary files from saved cases

For every case: verify Toxiproxy is clean (proxy enabled, ZERO toxics) -> baseline traffic -> validated run through
the normal runner (Chaos Toolkit + independent watchdog) -> recovery traffic -> verify Toxiproxy is clean again.
The campaign stops if Toxiproxy cannot be brought back to a clean state, so one bad case can't contaminate the next.

Output (campaigns/<id>/):
  runs/<run_id>/requests.jsonl   every request: phase, t_send, latency_ms, status, ok, error
  runs/<run_id>/summary.json     count, success rate, p50/p95/p99, recovery time per phase
  summary.jsonl                  one line per finished case (progress; used by --resume)
  summary.csv  cases.csv  by_config.csv  all_requests.csv  summary.json  report.md   (built at the end)
  campaign_all.json              EVERYTHING in one file: totals, by_config, every case summary + all its requests
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from . import lock
from .config import ROOT, load_safety, read_json, run_dir, runs_dir
from .stats import PHASES, label_phases, percentile, recovery, summarize_case, summarize_phase
from .toxiproxy import Toxiproxy
from .traffic import TrafficGenerator

# (fault_type, intensities). Units come from config/safety.yaml.
FAULT_MATRIX = [
    ("latency", [50, 100, 200, 400, 800, 1200, 1600, 2000]),
    ("timeout", [300, 800, 1500]),
    ("reset_peer", [50, 200, 800]),
    ("limit_data", [16, 64, 256, 1024]),
    ("bandwidth", [1, 10, 100]),
    ("connection-disabled", [1]),
]
TARGET = "podinfo-backend"


def build_cases(campaign_id: str, repeats: int, duration_s: float, only: str | None = None) -> list:
    """Round-robin over repeats so slow drift on the machine is spread across all configs."""
    configs = [(f, i) for f, ints in FAULT_MATRIX for i in ints if only in (None, f)]
    cases = []
    for rep in range(1, repeats + 1):
        for fault, intensity in configs:
            run_id = f"{campaign_id}-{len(cases) + 1:03d}"
            cases.append({
                "run_id": run_id, "fault_type": fault, "intensity": intensity, "repeat": rep,
                "plan": {"run_id": run_id, "candidate_id": f"{fault}-{intensity}-r{rep}", "target": TARGET,
                         "fault_type": fault, "intensity": intensity, "max_duration": duration_s,
                         "stop_thresholds": {"max_error_rate": 0.5, "max_latency_p95_ms": 5000},
                         "reason_codes": ["CAMPAIGN", "MATRIX"]}})
    return cases


def verify_clean(tp: Toxiproxy, target: str, settle_s: float, fix: bool = True) -> dict:
    """Clean = proxy enabled and no toxic of ANY kind. Waits for the watchdog, then force-cleans once."""
    deadline, st = time.time() + settle_s, None
    while True:
        try:
            st = tp.state(target)
        except Exception as e:                                  # Toxiproxy unreachable
            st = {"enabled": False, "toxics": [f"<unreachable: {e}>"]}
        if st["enabled"] and not st["toxics"]:
            return {"clean": True, "forced": False, "state": st}
        if time.time() >= deadline:
            break
        time.sleep(0.5)
    if not fix:
        return {"clean": False, "forced": False, "state": st}
    try:
        for name in st["toxics"]:
            tp.remove_toxic(target, name)
        if not st["enabled"]:
            tp.set_enabled(target, True)
        st2 = tp.state(target)
    except Exception as e:
        st2 = {"enabled": False, "toxics": [f"<unreachable: {e}>"]}
    return {"clean": st2["enabled"] and not st2["toxics"], "forced": True, "state_before_fix": st, "state": st2}


def _watch_phase(gen: TrafficGenerator, rd: Path, stop: threading.Event) -> None:
    """Live phase label for the 1-second aggregate rows (the saved per-request phases are re-derived exactly)."""
    while not stop.wait(0.05):
        run = read_json(rd / "run.json") or {}
        if run.get("removed_at") or run.get("state") in ("stopped", "cleanup_failed"):
            gen.phase = "recovery"
        elif run.get("state") in ("injecting", "injected"):
            gen.phase = "fault"


def run_case(case: dict, tp: Toxiproxy, safety: dict, args) -> dict:
    from . import runner
    plan, run_id = case["plan"], case["run_id"]
    t_start = time.time()
    stale = lock.owner(TARGET)
    if stale:
        lock.release(TARGET, stale)
    pre = verify_clean(tp, TARGET, settle_s=2.0)
    summary = {"run_id": run_id, "case": {k: case[k] for k in ("fault_type", "intensity", "repeat")},
               "plan": plan, "toxiproxy": {"clean_before": pre["clean"], "forced_before": pre["forced"]}}
    if not pre["clean"]:
        summary.update(status="not_run", stop_reason="toxiproxy_not_clean_before_case", abort_campaign=True)
        return summary

    rd = run_dir(run_id)
    gen = TrafficGenerator(safety["sandbox"]["frontend_url"].rstrip("/") + "/echo", run_id,
                           safety["sandbox"]["metrics_path"], rate_hz=args.rate)
    stop_watch = threading.Event()
    gen.start()
    threading.Thread(target=_watch_phase, args=(gen, rd, stop_watch), daemon=True).start()
    try:
        time.sleep(args.baseline)                                   # baseline phase (continues until injection)
        result = runner.run_plan(plan)                              # validate -> inject -> hold -> clean -> verify
        run = read_json(rd / "run.json") or {}
        removed_at = run.get("removed_at")
        if result["status"] != "blocked" and removed_at:            # keep measuring the recovery phase
            until, give_up = removed_at + args.recovery, removed_at + safety["recovery"]["timeout_s"]
            while True:
                time.sleep(0.25)
                t = time.time()
                if t < until:
                    continue
                recs = label_phases(gen.snapshot(), run.get("started_at"), removed_at)
                base = summarize_phase([r for r in recs if r["phase"] == "baseline"])["p95_ms"]
                if recovery(recs, removed_at, base)["recovered"] or t >= give_up:
                    break
    finally:
        stop_watch.set()
        gen.stop()

    timeline = {"injected_at": run.get("started_at"), "removed_at": run.get("removed_at")}
    records = gen.snapshot()
    stats = summarize_case(records, timeline)                        # also labels each record's phase
    records.sort(key=lambda r: r["seq"])
    with open(rd / "requests.jsonl", "w", encoding="utf8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    post = verify_clean(tp, TARGET, settle_s=8.0)
    summary.update(
        status=result["status"], stop_reason=result["stop_reason"], all_triggers=result.get("all_triggers", []),
        cleanup_verified=result["cleanup_verified"], recovery_result=result["recovery_result"],
        block_reasons=result.get("block_reasons", []), errors=result.get("errors", []),
        timeline=result.get("timeline", {}), duration_s=round(time.time() - t_start, 1),
        planned_fault_duration_s=plan["max_duration"], **stats)
    summary["toxiproxy"].update(clean_after=post["clean"], forced_after=post["forced"],
                                state_after=post["state"])
    summary["abort_campaign"] = not post["clean"]
    (rd / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf8")
    return summary


# ----------------------------------------------------------------------------------------------------------------
# aggregation / output files
# ----------------------------------------------------------------------------------------------------------------
def _load_summaries(cdir: Path) -> list:
    path = cdir / "summary.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf8").splitlines() if line.strip()]


def _load_requests(cdir: Path, run_id: str) -> list:
    path = cdir / "runs" / run_id / "requests.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf8").splitlines() if line.strip()]


def _write_csv(path: Path, rows: list, fields: list) -> None:
    with open(path, "w", newline="", encoding="utf8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def finalize(cdir: Path) -> dict:
    summaries = _load_summaries(cdir)
    case_rows, phase_rows, request_rows, groups, per_case_requests = [], [], [], {}, {}
    for s in summaries:
        c, tp_ = s["case"], s.get("toxiproxy", {})
        rec = s.get("recovery", {})
        base = {"run_id": s["run_id"], "fault_type": c["fault_type"], "intensity": c["intensity"],
                "repeat": c["repeat"], "status": s["status"], "stop_reason": s["stop_reason"]}
        case_rows.append({**base, "cleanup_verified": s.get("cleanup_verified"),
                          "recovered": rec.get("recovered"), "recovery_time_s": rec.get("recovery_time_s"),
                          "fault_duration_s": (s.get("timeline") or {}).get("fault_duration_s"),
                          "planned_fault_duration_s": s.get("planned_fault_duration_s"),
                          "toxiproxy_clean_before": tp_.get("clean_before"),
                          "toxiproxy_clean_after": tp_.get("clean_after"),
                          "baseline_p95_ms": s.get("phases", {}).get("baseline", {}).get("p95_ms"),
                          "fault_success_rate": s.get("phases", {}).get("fault", {}).get("success_rate"),
                          "fault_p95_ms": s.get("phases", {}).get("fault", {}).get("p95_ms")})
        for p in PHASES:
            ph = s.get("phases", {}).get(p)
            if ph:
                phase_rows.append({**base, "phase": p, **{k: ph[k] for k in (
                    "count", "success_count", "success_rate", "p50_ms", "p95_ms", "p99_ms", "mean_ms", "max_ms")},
                    "errors": json.dumps(ph["errors"]), "recovery_time_s": rec.get("recovery_time_s")})
        recs = _load_requests(cdir, s["run_id"])
        per_case_requests[s["run_id"]] = [{k: r[k] for k in ("seq", "phase", "t_rel", "latency_ms", "status", "ok", "error")}
                                         for r in recs]
        key = (c["fault_type"], c["intensity"])
        g = groups.setdefault(key, {"cases": [], "records": {p: [] for p in PHASES}})
        g["cases"].append(s)
        for r in recs:
            g["records"][r["phase"]].append(r)
            request_rows.append({"run_id": s["run_id"], "fault_type": c["fault_type"], "intensity": c["intensity"],
                                 "repeat": c["repeat"], **{k: r[k] for k in (
                                     "seq", "phase", "t_rel", "latency_ms", "status", "ok", "error")}})

    by_config = []
    for (fault, intensity), g in groups.items():
        rts = sorted(s["recovery"]["recovery_time_s"] for s in g["cases"]
                     if s.get("recovery", {}).get("recovery_time_s") is not None)
        row = {"fault_type": fault, "intensity": intensity, "cases": len(g["cases"]),
               "statuses": json.dumps(dict(Counter(s["status"] for s in g["cases"]))),
               "recovered_cases": sum(1 for s in g["cases"] if s.get("recovery", {}).get("recovered")),
               "recovery_p50_s": round(percentile(rts, 50), 2) if rts else None,
               "recovery_max_s": rts[-1] if rts else None,
               "all_toxiproxy_clean_after": all(s.get("toxiproxy", {}).get("clean_after") for s in g["cases"])}
        for p in PHASES:
            ph = summarize_phase(g["records"][p])
            row.update({f"{p}_{k}": ph[k] for k in ("count", "success_rate", "p50_ms", "p95_ms", "p99_ms")})
        by_config.append(row)

    _write_csv(cdir / "cases.csv", case_rows, list(case_rows[0]) if case_rows else [])
    _write_csv(cdir / "summary.csv", phase_rows, list(phase_rows[0]) if phase_rows else [])
    _write_csv(cdir / "all_requests.csv", request_rows, list(request_rows[0]) if request_rows else [])
    _write_csv(cdir / "by_config.csv", by_config, list(by_config[0]) if by_config else [])
    totals = {"cases": len(summaries), "statuses": dict(Counter(s["status"] for s in summaries)),
              "requests": len(request_rows),
              "toxiproxy_clean_before_all": all(s["toxiproxy"].get("clean_before") for s in summaries),
              "toxiproxy_clean_after_all": all(s["toxiproxy"].get("clean_after") for s in summaries)}
    (cdir / "summary.json").write_text(json.dumps({"totals": totals, "by_config": by_config, "cases": summaries},
                                                  indent=2), encoding="utf8")
    bundle = {"campaign": cdir.name, "totals": totals, "by_config": by_config,
              "cases": [{**s, "requests": per_case_requests[s["run_id"]]} for s in summaries]}
    (cdir / "campaign_all.json").write_text(json.dumps(bundle, separators=(",", ":")), encoding="utf8")
    _write_report(cdir, totals, by_config)
    return totals


def _write_report(cdir: Path, totals: dict, by_config: list) -> None:
    lines = [f"# Fault campaign {cdir.name}", "",
             f"- Cases: **{totals['cases']}**, per-request records: **{totals['requests']}**",
             f"- Outcomes: {totals['statuses']}",
             f"- Toxiproxy clean before every case: **{totals['toxiproxy_clean_before_all']}**, "
             f"after every case: **{totals['toxiproxy_clean_after_all']}**", "",
             "Latency percentiles use successful requests only. Recovery time = seconds from fault removal to the "
             "first of 5 consecutive good requests (within 1.5x baseline p95).", "",
             "| fault | intensity | cases | baseline p95 | fault success | fault p50 / p95 / p99 ms | recovery p50 / max s | outcomes |",
             "|---|---|---|---|---|---|---|---|"]
    for r in by_config:
        sr = r["fault_success_rate"]
        lines.append(f"| {r['fault_type']} | {r['intensity']} | {r['cases']} | {r['baseline_p95_ms']} | "
                     f"{'-' if sr is None else f'{sr:.0%}'} | {r['fault_p50_ms']} / {r['fault_p95_ms']} / "
                     f"{r['fault_p99_ms']} | {r['recovery_p50_s']} / {r['recovery_max_s']} | {r['statuses']} |")
    (cdir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf8")


# ----------------------------------------------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", default=time.strftime("c%m%d%H%M"))
    ap.add_argument("--repeats", type=int, default=7, help="repeats of each of the 22 fault configs (7 -> 154 cases)")
    ap.add_argument("--limit", type=int, help="run only the first N cases")
    ap.add_argument("--only", help="only this fault type")
    ap.add_argument("--duration", type=float, default=5, help="fault duration per case, seconds")
    ap.add_argument("--baseline", type=float, default=5, help="baseline seconds before injection")
    ap.add_argument("--recovery", type=float, default=4, help="minimum recovery-phase seconds after removal")
    ap.add_argument("--rate", type=float, default=10, help="requests per second")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--finalize-only", action="store_true")
    args = ap.parse_args(argv)

    cdir = ROOT / "campaigns" / args.id
    (cdir / "runs").mkdir(parents=True, exist_ok=True)
    os.environ["CHAOS_RUNS_DIR"] = str(cdir / "runs")                 # everything for this campaign stays together
    os.environ["CHAOS_METRICS_PATH"] = str(cdir / "metrics.jsonl")
    os.environ["CHAOS_QUIET"] = "1"
    if args.finalize_only:
        print(json.dumps(finalize(cdir), indent=2))
        return 0

    safety = load_safety()
    tp = Toxiproxy(safety["sandbox"]["toxiproxy_url"])
    cases = build_cases(args.id, args.repeats, args.duration, args.only)
    if args.limit:
        cases = cases[:args.limit]
    (cdir / "cases.json").write_text(json.dumps(cases, indent=2), encoding="utf8")
    done = {s["run_id"] for s in _load_summaries(cdir)} if args.resume else set()
    if not args.resume and (cdir / "summary.jsonl").exists():
        sys.exit(f"{cdir} already has results; use --resume or a different --id")
    todo = [c for c in cases if c["run_id"] not in done]
    print(f"campaign {args.id}: {len(cases)} cases ({len(todo)} to run) -> {cdir}", flush=True)

    start_state = verify_clean(tp, TARGET, settle_s=2.0)
    if not start_state["clean"]:
        sys.exit(f"Toxiproxy is not clean at start: {start_state['state']}")
    watchdog = subprocess.Popen([sys.executable, "-m", "chaos.watchdog"], cwd=ROOT, env=os.environ.copy(),
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    code = 0
    try:
        for n, case in enumerate(todo, 1):
            s = run_case(case, tp, safety, args)
            with open(cdir / "summary.jsonl", "a", encoding="utf8") as f:
                f.write(json.dumps(s) + "\n")
            fp = s.get("phases", {}).get("fault", {})
            sr = fp.get("success_rate")
            print(f"[{len(done) + n:03d}/{len(cases)}] {case['fault_type']:<19} {case['intensity']:>5} r{case['repeat']}"
                  f" -> {s['status']:<9} {s['stop_reason']:<26}"
                  f" fault n={fp.get('count', 0):<3} ok={'-' if sr is None else f'{sr:.0%}':<4} p95={fp.get('p95_ms')}"
                  f" | recovery {s.get('recovery', {}).get('recovery_time_s')}s"
                  f" | toxiproxy clean {s['toxiproxy'].get('clean_before')}/{s['toxiproxy'].get('clean_after')}"
                  f" | {s.get('duration_s')}s", flush=True)
            if s.get("abort_campaign"):
                print("STOP: Toxiproxy could not be returned to a clean state; not running further cases.")
                code = 2
                break
    except KeyboardInterrupt:
        print("interrupted; use --resume to continue")
        code = 130
    finally:
        watchdog.terminate()
        try:
            verify_clean(tp, TARGET, settle_s=1.0)                    # leave the sandbox clean
        except Exception:
            pass
    totals = finalize(cdir)
    print("\nfinal:", json.dumps(totals))
    print(f"files in {cdir}: summary.csv cases.csv by_config.csv all_requests.csv summary.json report.md")
    return code


if __name__ == "__main__":
    sys.exit(main())
