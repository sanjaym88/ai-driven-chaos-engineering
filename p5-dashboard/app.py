"""P5 dashboard - run with:  streamlit run app.py

Reads ONLY the team's record files (via p5.adapters). Nothing here invents a number:
every figure comes from p5.facts, the same source the reports and Slack use.
"""
from __future__ import annotations

import inspect
from datetime import timedelta

import altair as alt
import pandas as pd
import streamlit as st

from p5 import config
from p5.adapters import load_map, load_runs
from p5.facts import build_facts, fmt
from p5.findings import RULES_VERSION, evaluate
from p5.notify import Outbox, Worker, default_sender
from p5.report import headline, render_html, render_md

st.set_page_config(page_title="Chaos runs", page_icon=":material/bolt:", layout="wide")

SERIES = "#2a78d6"
STATUS = {  # status palette: color + icon + label, never color alone
    "completed": ("green", ":material/check_circle:"),
    "aborted": ("orange", ":material/pan_tool:"),
    "failed": ("red", ":material/error:"),
    "blocked": ("gray", ":material/block:"),
}


def full_width(fn) -> dict:
    """Streamlit renamed `use_container_width=True` to `width="stretch"` around v1.50.
    Pick whichever this installed version understands, so the app runs on old and new."""
    try:
        p = inspect.signature(fn).parameters.get("width")
        if p is not None and isinstance(p.default, str):
            return {"width": "stretch"}
    except (TypeError, ValueError):
        pass
    return {"use_container_width": True}


@st.cache_data(ttl=3, show_spinner=False)
def _runs(path: str):
    return load_runs(config.runs_dir())


def status_badge(status: str | None):
    color, icon = STATUS.get(status or "", ("gray", ":material/help:"))
    st.badge(status or "unknown", icon=icon, color=color)


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.title("Chaos runs")
    page = st.radio("View", ["Runs", "Service map", "Notifications"], label_visibility="collapsed")
    live = st.toggle("Auto-refresh every 5 s", value=False, help="Turn on while experiments are running")
    st.divider()
    st.caption("Data source")
    st.code(str(config.runs_dir()), language=None)
    st.caption(f"Findings: {RULES_VERSION}")
    if not config.slack_webhook():
        st.caption("Slack: dry-run (no webhook set)")


# ------------------------------------------------------------------ charts
def timeline_chart(run) -> list[alt.Chart] | None:
    if len(run.samples) < 2:
        return None
    t0 = run.samples[0].t
    df = pd.DataFrame([{
        "t_s": (x.t - t0).total_seconds(), "time": x.t.strftime("%H:%M:%S"), "phase": x.phase,
        "p95_ms": x.p95_ms, "error_rate": (x.errors / x.requests) if x.requests else None,
        "requests": x.requests, "errors": x.errors} for x in run.samples])
    bands = (df.groupby("phase", sort=False)["t_s"].agg(["min", "max"]).reset_index()
             .query("phase != 'baseline'"))
    step = df["t_s"].diff().median() or 0
    bands["max"] = bands["max"] + step
    x = alt.X("t_s:Q", title="seconds since run start", axis=alt.Axis(grid=False))
    band = alt.Chart(bands).mark_rect(opacity=0.12, color="#52514e").encode(
        x="min:Q", x2="max:Q", tooltip=[alt.Tooltip("phase:N", title="window")])
    label = alt.Chart(bands).mark_text(align="left", baseline="top", dx=4, dy=4, color="#52514e").encode(
        x="min:Q", y=alt.value(0), text="phase:N")
    tips = [alt.Tooltip("time:N"), alt.Tooltip("phase:N"), alt.Tooltip("p95_ms:Q", title="p95 ms", format=".1f"),
            alt.Tooltip("error_rate:Q", title="error rate", format=".1%"), "requests:Q", "errors:Q"]

    def panel(field, title, fmt_axis, height, domain=None):
        scale = alt.Scale(domain=domain) if domain else alt.Scale(zero=True)
        line = alt.Chart(df).mark_line(color=SERIES, strokeWidth=2).encode(
            x=x, y=alt.Y(f"{field}:Q", title=title, scale=scale,
                         axis=alt.Axis(format=fmt_axis, grid=True, tickCount=3)))
        pts = alt.Chart(df).mark_circle(size=90, opacity=0).encode(x=x, y=f"{field}:Q", tooltip=tips)
        return (band + label + line + pts).properties(height=height)

    xmax = float(df["t_s"].max() + step)
    x.scale = alt.Scale(domain=[0, xmax])           # same time axis on both panels
    return [panel("p95_ms", "p95 latency (ms)", ",.0f", 190),
            panel("error_rate", "error rate", ".0%", 120, domain=[0, 1])]


# ------------------------------------------------------------------ pages
def run_detail(run):
    f = build_facts(run)
    c1, c2 = st.columns([3, 1])
    with c1:
        st.subheader(f"{run.run_id} · {f['fault_type']} on {f['target'].replace('->', ' → ')}")
        status_badge(run.status)
        st.write(headline(f))
    with c2:
        st.download_button("Report (HTML)", render_html(run), f"{run.run_id}.html", "text/html",
                           icon=":material/download:", **full_width(st.download_button))
        st.download_button("Report (Markdown)", render_md(run), f"{run.run_id}.md", "text/markdown",
                           **full_width(st.download_button))

    b, fl = f["phases"]["baseline"], f["phases"]["fault"]
    m = st.columns(4)
    dp = (fl["p95_ms"].value - b["p95_ms"].value) if fl["p95_ms"].known and b["p95_ms"].known else None
    de = (fl["error_rate"].value - b["error_rate"].value) if fl["error_rate"].known and b["error_rate"].known else None
    m[0].metric("p95 during fault (ms)", fmt(fl["p95_ms"], 1).replace(" ms", ""), help=fl["p95_ms"].source,
                delta=f"{dp:+.1f} vs baseline" if dp else None, delta_color="inverse")
    m[1].metric("Errors during fault", fmt(fl["error_rate"]), help=fl["error_rate"].source,
                delta=f"{de * 100:+.1f} pts" if de else None, delta_color="inverse")
    rec_word = {"not_recovered": "failed", "not_applicable": "n/a"}.get(f["recovery_status"], f["recovery_status"])
    m[2].metric("Recovery", fmt(f["recovery_time_s"], 1) if f["recovery_time_s"].known else rec_word,
                help=f"recovery_result.status = {f['recovery_status']}")
    m[3].metric("Cleanup verified", fmt(f["cleanup_verified"]))

    charts = timeline_chart(run)
    if charts:
        for c in charts:
            st.altair_chart(c, **full_width(st.altair_chart))
    else:
        st.info("Not enough metric samples to draw a chart.")

    left, right = st.columns(2)
    with left:
        st.markdown("**Findings**")
        for x in evaluate(f):
            color = {"critical": "red", "high": "orange", "medium": "violet", "unknown": "gray"}.get(x.severity, "blue")
            with st.container(border=True):
                st.badge(x.severity, color=color)
                st.markdown(f"**{x.title}** · `{x.rule_id}`  \n{x.detail}  \n**Fix:** {x.fix}")
    with right:
        st.markdown("**What was tested**")
        st.dataframe(pd.DataFrame([
            ("Stop reason", f["stop_reason"]),
            ("Stopped by", f["stopped_by"] or "-"),
            ("Intensity", f["intensity"]),
            ("Max duration", fmt(f["max_duration_s"])),
            ("Thresholds", f"p95 > {fmt(f['threshold_p95_ms'])}, errors > {fmt(f['threshold_error_rate'])}"),
            ("Reason codes", ", ".join(f["reason_codes"]) or "unknown"),
            ("Planner", f"{f['planner']} · rank {fmt(f['rank'])} · score {fmt(f['score'], 2)}"),
            ("Failure detail", f["failure_detail"] or "-"),
        ], columns=["", "value"]), hide_index=True, **full_width(st.dataframe))
        st.markdown("**Measurements**")
        rows = [{"": k, **{ph: fmt(f["phases"][ph][key], 1) for ph in ("baseline", "fault", "recovery")}}
                for k, key in (("requests", "requests"), ("errors", "errors"), ("error rate", "error_rate"),
                               ("p50 ms (median)", "p50_ms"), ("p95 ms (worst)", "p95_ms"))]
        st.dataframe(pd.DataFrame(rows), hide_index=True, **full_width(st.dataframe))
        st.markdown("**Evidence**")
        for e in f["evidence"]:
            p = run.folder / e["path"]
            st.markdown(f"- `{e['path']}` ({e['type']}){'' if p.exists() else ' - _file not found_'}")
    if run.problems:
        st.warning("Data problems: " + "; ".join(run.problems))


def runs_page():
    runs = _runs(str(config.runs_dir()))
    if not runs:
        st.info(f"No runs yet in `{config.runs_dir()}`.")
        return
    counts = {s: sum(r.status == s for r in runs) for s in STATUS}
    cols = st.columns(5)
    cols[0].metric("Runs", len(runs))
    for c, (s, n) in zip(cols[1:], counts.items()):
        c.metric(s.capitalize(), n)

    table = pd.DataFrame([{
        "run": r.run_id, "status": r.status or "unknown",
        "target": r.plan.target if r.plan else "unknown",
        "fault": r.plan.fault_type if r.plan else "unknown",
        "stop reason": r.stop_reason or "unknown",
        "cleanup": fmt(build_facts(r)["cleanup_verified"]),
        "recovery": (f"{r.recovery_time_s:.1f} s" if r.recovery_time_s is not None else (r.recovery_status or "unknown")),
        "started (UTC)": (r.started_at.strftime("%Y-%m-%d %H:%M:%S") if r.started_at else "unknown"),
    } for r in runs])
    event = st.dataframe(table, hide_index=True, **full_width(st.dataframe), on_select="rerun",
                         selection_mode="single-row", key="runs_table")
    chosen = st.query_params.get("run")
    if event.selection.rows:
        chosen = table.iloc[event.selection.rows[0]]["run"]
        st.query_params["run"] = chosen
    run = next((r for r in runs if r.run_id == chosen), runs[0])
    st.caption("Select a row to open a run. Newest first.")
    st.divider()
    run_detail(run)


def map_page():
    sm = load_map(config.map_path())
    if not sm:
        st.info(f"No service map at `{config.map_path()}` yet (P1).")
        return
    names = {s["id"]: s.get("name") or s["id"] for s in sm.services}
    dot = ["digraph G {", 'rankdir=LR; node [shape=box, style="rounded", fontname="Helvetica", fontsize=11];']
    for s in sm.services:
        dot.append(f'"{s["id"]}" [label="{names[s["id"]]}\\n{s.get("health_check") or ""}"];')
    for e in sm.edges:
        lbl = f' [label="via {e["via"]}", fontsize=10]' if e.get("via") else ""
        style = "" if e.get("verified", True) else ' [style=dashed, label="unverified"]'
        dot.append(f'"{e["from"]}" -> "{e["to"]}"{lbl or style};')
    dot.append("}")
    st.subheader("Service map")
    st.graphviz_chart("\n".join(dot), **full_width(st.graphviz_chart))
    if sm.request_paths:
        st.markdown("**Request paths**")
        st.dataframe(pd.DataFrame([{"name": p.get("name"), "endpoint": p.get("endpoint"),
                                    "path": " → ".join(p.get("path", []))} for p in sm.request_paths]),
                     hide_index=True, **full_width(st.dataframe))
    for n in sm.notes:
        st.caption(f"• {n}")
    st.caption(f"Source: {sm.source or 'unknown'} · reviewed by {sm.reviewed_by or 'unknown'}")


def notifications_page():
    st.subheader("Slack notifications")
    st.caption("One event per final run (id = run_id:status). Delivery runs in its own process "
               "(`python -m p5 worker`) and never blocks cleanup.")
    ob = Outbox()
    if st.button("Scan runs and deliver now", icon=":material/send:"):
        w = Worker(ob, default_sender())
        q = w.scan(config.runs_dir())
        st.success(f"Queued {q} new event(s); delivery: {w.deliver()}")
    rows = ob.all()
    if rows:
        df = pd.DataFrame(rows)
        for c in ("created_at", "sent_at"):
            df[c] = pd.to_datetime(df[c], unit="s", utc=True).dt.strftime("%H:%M:%S")
        st.dataframe(df, hide_index=True, **full_width(st.dataframe))
    else:
        st.info("Outbox empty.")
    log = config.out_dir() / "slack_dry_run.log"
    if log.exists():
        with st.expander("Dry-run log (what would have been posted)"):
            st.code(log.read_text()[-4000:], language=None)


PAGES = {"Runs": runs_page, "Service map": map_page, "Notifications": notifications_page}
if live:
    st.fragment(PAGES[page], run_every=timedelta(seconds=5))()
else:
    PAGES[page]()
