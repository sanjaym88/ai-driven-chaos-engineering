"""Static SVG charts for reports (no JS, works offline, prints well).

Two small charts on the SAME time axis - p95 latency (ms) and error rate (%) - rather
than one chart with two y-scales. The fault window is shaded; each point has a native
hover tooltip (<title>) with its exact recorded values.
"""
from __future__ import annotations

from html import escape

from .adapters import Run

def nice_max(v: float) -> float:
    """Round an axis maximum up to 1, 2, 2.5 or 5 x 10^n so tick labels are readable."""
    import math
    if v <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * mag:
            return m * mag
    return 10 * mag


PHASE_FILL = {"baseline": "none", "fault": "var(--band-fault)", "recovery": "var(--band-rec)"}


def _panel(run: Run, title: str, values: list[float | None], vmax: float, unit_fmt, width: int, height: int,
           show_phase_labels: bool) -> str:
    s = run.samples
    L, R, T, B = 56, 16, 34, 22
    t0, t1 = s[0].t, s[-1].t
    span = max((t1 - t0).total_seconds(), 1)
    X = lambda t: L + (width - L - R) * (t - t0).total_seconds() / span
    Y = lambda v: T + (height - T - B) * (1 - v / vmax)

    bands, cur, start = [], s[0].phase, s[0].t
    for x in s[1:] + [None]:
        if x is None or x.phase != cur:
            bands.append((cur, start, x.t if x else t1))
            if x:
                cur, start = x.phase, x.t
    parts = [f'<text x="{L}" y="14" class="ttl">{escape(title)}</text>']
    for ph, a, b in bands:
        if PHASE_FILL.get(ph, "none") != "none":
            parts.append(f'<rect x="{X(a):.1f}" y="{T}" width="{max(1, X(b) - X(a)):.1f}" height="{height - T - B}" '
                         f'style="fill:{PHASE_FILL[ph]}"/>')
        if show_phase_labels:
            parts.append(f'<text x="{X(a) + 4:.1f}" y="{T - 4}" class="lbl">{escape(ph)}</text>')
    for frac in (0.5, 1.0):
        y = Y(vmax * frac)
        parts.append(f'<line x1="{L}" y1="{y:.1f}" x2="{width - R}" y2="{y:.1f}" class="grid"/>'
                     f'<text x="{L - 6}" y="{y + 4:.1f}" class="lbl" text-anchor="end">{unit_fmt(vmax * frac)}</text>')
    parts.append(f'<line x1="{L}" y1="{height - B}" x2="{width - R}" y2="{height - B}" class="axis"/>'
                 f'<text x="{L - 6}" y="{height - B + 4}" class="lbl" text-anchor="end">0</text>')
    pts = [(X(x.t), Y(v), x, v) for x, v in zip(s, values) if v is not None]
    parts.append('<polyline class="series" points="' + " ".join(f"{a:.1f},{b:.1f}" for a, b, _, _ in pts) + '"/>')
    for a, b, x, v in pts:   # hit targets bigger than the line, with exact values
        parts.append(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="6" class="hit"><title>{x.t:%H:%M:%S} · {x.phase} · '
                     f'{unit_fmt(v)} · {x.requests} req, {x.errors} err</title></circle>')
    return (f'<svg viewBox="0 0 {width} {height}" class="chart" role="img" aria-label="{escape(title)} over time">'
            + "".join(parts) + "</svg>")


def svg_timeline(run: Run, width: int = 720) -> str:
    s = run.samples
    if len(s) < 2:
        return '<p class="muted">Not enough metric samples to draw a chart.</p>'
    p95 = [x.p95_ms for x in s]
    known = [v for v in p95 if v is not None] or [1.0]
    err = [(x.errors / x.requests) if x.requests else None for x in s]
    span = (s[-1].t - s[0].t).total_seconds()
    lat = _panel(run, "p95 latency (ms)", p95, nice_max(max(known) * 1.05), lambda v: f"{v:.0f} ms", width, 150, True)
    er = _panel(run, "Error rate", err, 1.0, lambda v: f"{v * 100:.0f}%", width, 120, False)
    return lat + er + f'<p class="muted" style="font-size:12px;margin:2px 0 0">Time axis: 0 – {span:.0f} s from run start · shaded = fault / recovery windows · hover a point for exact values</p>'
