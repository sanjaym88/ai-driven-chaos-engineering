"""Render the fixed report template (HTML + Markdown) for a run."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from . import config
from .adapters import PHASES, Run
from .chart import svg_timeline
from .facts import Fact, build_facts, fmt
from .findings import RULES_VERSION, evaluate

TEMPLATE_VERSION = "report-template v1"
_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                   autoescape=True, undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
_env_md = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=False, undefined=StrictUndefined)


def headline(f: dict) -> str:
    """One fixed sentence per status - no free text."""
    rec = f["recovery_status"]
    rec_txt = f"recovered in {fmt(f['recovery_time_s'], 1)}" if f["recovery_time_s"].known else f"recovery: {rec}"
    s = f["status"]
    if s == "completed":
        return f"The fault ran for its planned duration, was removed, and the app {rec_txt}."
    if s == "aborted":
        return (f"Stopped early by the {f['stopped_by'] or 'safety system'}: {f['stop_reason']}. "
                f"Cleanup verified: {fmt(f['cleanup_verified'])}; {rec_txt}.")
    if s == "failed":
        return f"Run FAILED and needs attention: {f['failure_detail'] or f['stop_reason']}."
    if s == "blocked":
        return f"Blocked before injection: {f['stop_reason']}. No fault was injected."
    return "Run status is unknown (result.json missing or incomplete)."


def _sources(f: dict) -> list[tuple[str, Fact]]:
    rows = [(k, v) for k, v in f.items() if isinstance(v, Fact)]
    for ph in PHASES:
        rows += [(f"{ph}.{k}", v) for k, v in f["phases"][ph].items()]
    return rows


def context(run: Run) -> dict:
    f = build_facts(run)
    return {"f": f, "fmt": fmt, "phases": PHASES, "findings": evaluate(f), "headline": headline(f),
            "chart": svg_timeline(run), "sources": _sources(f),
            "rules_version": RULES_VERSION, "template_version": TEMPLATE_VERSION}


def render_html(run: Run) -> str:
    return _env.get_template("report.html.j2").render(**context(run))


def render_md(run: Run) -> str:
    return _env_md.get_template("report.md.j2").render(**context(run))


def write_reports(run: Run) -> list[Path]:
    d = config.out_dir() / "reports"
    d.mkdir(parents=True, exist_ok=True)
    h, m = d / f"{run.run_id}.html", d / f"{run.run_id}.md"
    h.write_text(render_html(run))
    m.write_text(render_md(run))
    return [h, m]
