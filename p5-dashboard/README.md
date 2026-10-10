# P5 — Dashboard, Reports & Slack (Bosch chaos-engineering hackathon)

P5 turns the team's run records into things people can see:

| Component | What it does | Run it |
|---|---|---|
| **Dashboard** | Run list, run detail (charts with fault window shaded, findings, evidence), service map, notification log | `streamlit run app.py` |
| **Reports** | Fixed HTML + Markdown template per run, rule-based findings, "unknown" for missing data | `python -m p5 report --all` |
| **Slack notifier** | Outbox with event IDs, retry + backoff, dedup, dry-run mode; separate process, never blocks cleanup | `python -m p5 worker` |
| **Contract check** | Validates the team's files against the agreed formats | `python -m p5 validate <runs_dir>` |

**Rule of P5:** every number shown comes from the run records (`p5/facts.py`). Nothing is generated, estimated or written by an LLM.

---

## 1. Setup (5 minutes)

```bash
cd p5-dashboard
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -q                                               # 21 tests, should all pass
streamlit run app.py                                    # opens http://localhost:8501
```

Out of the box it reads the **sample runs** in `fixtures/` — one of each status:

| Run | Status | Story |
|---|---|---|
| run-0001 | completed | 800 ms delay on frontend→backend for 30 s, recovered in 4 s |
| run-0002 | aborted | 2500 ms delay breached the p95 limit, watchdog stopped it after 6 s |
| run-0003 | failed | connection reset; cleanup not verified, app did not recover → urgent Slack |
| run-0004 | blocked | stale baseline, nothing was injected |

Regenerate them any time: `python tools/make_fixtures.py` (deterministic).

## 2. How the code is organised

```
p5/
  adapters.py   <- THE ONLY FILE THAT KNOWS THE TEAM'S FIELD NAMES (change this at merge)
  facts.py      every number + where it came from; phase summaries; fmt() -> "unknown" for missing
  findings.py   versioned rule table (F0..F7): when it applies, what it found, the fix
  report.py     fixed one-sentence headline per status + renders templates/
  chart.py      static SVG charts for the report (two single-axis panels, hover tooltips)
  notify.py     Slack: message templates, SQLite outbox, retry/backoff, dedup, dry-run
  config.py     paths + Slack webhook from environment variables
  templates/    report.html.j2, report.md.j2
app.py          Streamlit dashboard
contracts/      CONTRACTS.md + JSON schemas = the formats P5 expects from P1-P4
fixtures/       sample service map + 4 sample runs
tests/          the P5 evaluations from the team plan
```

Data flows one way: **raw files → adapters → facts → (report | dashboard | Slack)**. So all three always show the same numbers.

## 3. Working independently — your order of work

You can finish everything below without anyone else:

1. **Read `contracts/CONTRACTS.md`.** This is your proposal for the shared formats. Share it in the team chat early so others build towards it (that makes your merge trivial).
2. **Run it on the fixtures** and click through all four runs, the Service map and Notifications pages.
3. **Customise** (all safe to change):
   - wording of findings and fixes → `p5/findings.py` (bump `RULES_VERSION`)
   - report layout → `p5/templates/`
   - Slack wording → `message_for()` in `p5/notify.py`
   - dashboard pages → `app.py`
4. **Re-run `pytest -q` after every change.** The tests are your safety net — especially *report numbers match records*.
5. **Try Slack for real** (optional): create a Slack *Incoming Webhook*, then
   ```bash
   export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
   python -m p5 worker --once
   ```
   Without it, messages go to `out/slack_dry_run.log` — perfectly fine for the demo.
6. **Simulate Slack being down:** `SLACK_WEBHOOK_URL=http://localhost:9 python -m p5 worker --once`, then `python -m p5 outbox` → events stay `pending` with an error and retry later; nothing else is affected.

## 4. Merging with the team (checkpoint M4)

Do this when P3/P4 have produced at least one real run.

**Step 1 — Put P5 in the shared repo**
```bash
# in the team repo
git checkout -b p5-dashboard
cp -r /path/to/p5-dashboard ./p5        # or keep it as its own folder
git add p5 && git commit -m "P5: dashboard, reports, Slack notifier"
git push -u origin p5-dashboard           # open a pull request
```

**Step 2 — Point P5 at the real files** (no code change):
```bash
export P5_RUNS_DIR=/path/to/team/runs
export P5_MAP_PATH=/path/to/team/service-map.json
```

**Step 3 — Check the formats**
```bash
python -m p5 validate $P5_RUNS_DIR
```
- `OK` → open the dashboard, you're merged.
- Problems listed → for each one, decide *with the owner*: either they rename their field, or you map it in **`p5/adapters.py`**. Examples:

| Validate says | Fix in `adapters.py` |
|---|---|
| `result.json: 'status' is a required property` and they write `state` | `status = res.get("status") or res.get("state")` |
| latency is `latency_p95` at top level | in `adapt_sample`: `p95 = raw.get("latency_p95")` |
| timestamps are epoch numbers | already supported by `parse_time` |
| `max_duration` instead of `max_duration_s` | already supported |
| phase names `steady`/`inject` | map them: `{"steady": "baseline", "inject": "fault"}` |

**Step 4 — Agree the summary numbers with P4.** P5 defines phase summaries in `p5/facts.py` (p95 = worst interval p95, p50 = median, error rate = sum errors / sum requests). If P4 calculates them differently, use P4's definition (or import P4's function in `phase_summary`) so the dashboard and P4's evaluations agree.

**Step 5 — Re-run tests and add one real run as a test fixture**
```bash
cp -r $P5_RUNS_DIR/<a-real-run> fixtures/runs/
pytest -q
```

**Step 6 — Start the notifier next to the experiment** (separate terminal; P3 changes nothing):
```bash
python -m p5 worker            # watches P5_RUNS_DIR, one message per finished run
```

**Merge checklist**
- [ ] `python -m p5 validate` → OK on real runs
- [ ] all four statuses appear correctly when they happen for real
- [ ] numbers on dashboard = numbers in P4's records (spot-check one run)
- [ ] stopping the notifier mid-experiment changes nothing for P3's cleanup
- [ ] `pytest -q` green

## 5. Demo (≤ 3 minutes) — what P5 shows

1. **Service map** page: the app and the Toxiproxy fault point (10 s).
2. **Runs** page → a **completed** run: p95 jumps during the shaded fault window, recovers; findings with fixes (40 s).
3. An **aborted** run: "stopped by watchdog: threshold_breach…", cleanup verified, recovered — *the safety story* (40 s).
4. **Notifications** page / Slack: one message per run; failed runs are urgent (20 s).
5. Download the HTML report: "every number is traceable — open *Where every number comes from*" (20 s).

Turn on **Auto-refresh** in the sidebar while a live experiment runs.

## 6. Settings

| Variable | Default | Meaning |
|---|---|---|
| `P5_RUNS_DIR` | `fixtures/runs` | runs folder (one sub-folder per run) |
| `P5_MAP_PATH` | `fixtures/service-map.json` | service map from P1 |
| `P5_OUT_DIR` | `out/` | reports, outbox.db, dry-run log |
| `P5_REPORT_BASE_URL` | `http://localhost:8501` | link used in Slack messages |
| `SLACK_WEBHOOK_URL` | unset → dry-run | Slack incoming webhook |
