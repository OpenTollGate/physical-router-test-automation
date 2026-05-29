#!/usr/bin/env python3
"""Render a self-contained HTML report from canonical run.json + summary.json."""

import argparse
import html
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MONTHS = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def fmt_timestamp(iso_str):
    """Format ISO timestamp to 'May 16, 2026 17:26 UTC'."""
    if not iso_str:
        return "N/A"
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            dt = datetime.strptime(iso_str, fmt)
            return f"{MONTHS[dt.month]} {dt.day}, {dt.year} {dt.hour:02d}:{dt.minute:02d} UTC"
        except ValueError:
            continue
    return iso_str


def fmt_duration(ms):
    """Convert milliseconds to 'Xm Ys' or 'Xs'."""
    if ms is None:
        return "N/A"
    s = int(ms) / 1000
    if s < 1:
        return "0s"
    m, s = divmod(int(s), 60)
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def esc(text):
    if text is None:
        return "N/A"
    return html.escape(str(text))


def status_color(status):
    colors = {
        "passed": "#137333", "failed": "#d93025", "errored": "#ea4335",
        "partial": "#b06000", "cancelled": "#80868b",
    }
    return colors.get(status, "#80868b")


def status_bg(status):
    bgs = {
        "passed": "#e6f4ea", "failed": "#fce8e8", "errored": "#fce8e8",
        "partial": "#fef7e0", "cancelled": "#f1f3f4",
    }
    return bgs.get(status, "#f1f3f4")


def badge(status):
    c = status_color(status)
    bg = status_bg(status)
    label = status.upper()
    return Markup(
        f'<span style="display:inline-block;font-size:1.1rem;font-weight:700;'
        f'padding:6px 18px;border-radius:6px;color:{c};background:{bg};'
        f'letter-spacing:0.5px">{esc(label)}</span>'
    )


def small_badge(status):
    c = status_color(status)
    bg = status_bg(status)
    return Markup(
        f'<span style="display:inline-block;font-size:0.75rem;font-weight:600;'
        f'padding:2px 10px;border-radius:4px;color:{c};background:{bg}">'
        f'{esc(status.upper())}</span>'
    )


def gh_repo_url(repo):
    if repo and repo != "unknown" and "/" in repo:
        return f"https://github.com/{repo}"
    return None


def gh_commit_url(repo, commit):
    base = gh_repo_url(repo)
    if base and commit and commit != "unknown":
        return f"{base}/commit/{commit}"
    return None


def gh_pr_url(repo, pr):
    base = gh_repo_url(repo)
    if base and pr:
        return f"{base}/pull/{pr}"
    return None


def truncate(text, limit=200):
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

CSS = """\
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
background:#f5f5f7;color:#1a1a2e;line-height:1.6;padding:1rem}
.mono{font-family:"SF Mono",SFMono-Regular,Consolas,"Liberation Mono",Menlo,monospace}
.container{max-width:1100px;margin:0 auto}
.card{background:#fff;border-radius:8px;padding:1.5rem;margin-bottom:1.25rem;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.section-title{font-size:1rem;font-weight:700;margin-bottom:0.75rem;color:#1a1a2e;
border-bottom:2px solid #e8eaed;padding-bottom:0.4rem}
h1{font-size:1.5rem;font-weight:700;margin-bottom:0.5rem}
.meta-grid{display:grid;grid-template-columns:140px 1fr;gap:4px 12px;font-size:0.875rem}
.meta-grid .label{color:#80868b;font-weight:500}
.meta-grid .value{color:#1a1a2e}
a{color:#1a73e8;text-decoration:none}
a:hover{text-decoration:underline}
.runners-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:1rem}
.runner-card{background:#fff;border-radius:8px;padding:1.25rem;box-shadow:0 1px 3px rgba(0,0,0,.08);
border-left:4px solid #e8eaed}
.runner-card.passed{border-left-color:#137333}
.runner-card.failed,.runner-card.errored{border-left-color:#d93025}
.runner-card .runner-name{font-size:1rem;font-weight:700;margin-bottom:0.5rem}
.runner-card .runner-meta{font-size:0.8rem;color:#80868b;margin-bottom:0.75rem}
.counts-row{display:flex;gap:0.75rem;flex-wrap:wrap;font-size:0.8rem;font-weight:600}
.counts-row .c-pass{color:#137333}.counts-row .c-fail{color:#d93025}
.counts-row .c-skip{color:#80868b}.counts-row .c-flaky{color:#b06000}
.counts-row .c-err{color:#ea4335}
.artifacts-links{margin-top:0.6rem;font-size:0.8rem}
.artifacts-links a{margin-right:0.75rem}
table{width:100%;border-collapse:collapse;font-size:0.875rem}
th{background:#f1f3f4;font-weight:600;text-align:left;padding:8px 10px;
border-bottom:2px solid #dadce0;white-space:nowrap}
td{padding:8px 10px;border-bottom:1px solid #f1f3f4}
tr:hover td{background:#fafbfc}
.mono-cell{font-family:"SF Mono",SFMono-Regular,Consolas,monospace;font-size:0.8rem}
details{margin-bottom:0}
details>summary{cursor:pointer;font-size:1rem;font-weight:700;padding:0.4rem 0;
border-bottom:2px solid #e8eaed;margin-bottom:0.75rem;user-select:none}
details>summary:hover{color:#1a73e8}
.footer{font-size:0.75rem;color:#80868b;text-align:center;padding:1.5rem 0 0.5rem;
border-top:1px solid #e8eaed;margin-top:1rem}
.native-links{list-style:none;padding:0}
.native-links li{padding:4px 0;font-size:0.875rem}
@media(max-width:700px){
.meta-grid{grid-template-columns:1fr;gap:2px}
.runners-grid{grid-template-columns:1fr}
body{padding:0.5rem}
}
"""


def build_environment():
    templates_dir = Path(__file__).resolve().parents[1] / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "htm"]),
        variable_start_string="<!--[[",
        variable_end_string="]]-->",
    )
    env.filters["esc"] = esc
    env.filters["fmt_timestamp"] = fmt_timestamp
    env.filters["fmt_duration"] = fmt_duration
    env.filters["truncate"] = truncate
    env.globals.update(
        badge=badge,
        small_badge=small_badge,
        gh_repo_url=gh_repo_url,
        gh_commit_url=gh_commit_url,
        gh_pr_url=gh_pr_url,
        truncate=truncate,
        fmt_timestamp=fmt_timestamp,
        fmt_duration=fmt_duration,
        esc=esc,
        status_color=status_color,
        status_bg=status_bg,
        render_header=lambda run: Markup(render_header(run)),
        render_sut=lambda run: Markup(render_sut(run)),
        render_lab=lambda run: Markup(render_lab(run)),
        render_runner_card=lambda runner: Markup(render_runner_card(runner)),
        render_runners=lambda run: Markup(render_runners(run)),
        render_failed=lambda run, summary: Markup(render_failed(run, summary)),
        render_skipped=lambda run, summary: Markup(render_skipped(run, summary)),
        render_native_links=lambda run: Markup(render_native_links(run)),
        render_pipeline_timing=lambda run: Markup(render_pipeline_timing(run)),
        render_footer=lambda run, generated_at: Markup(render_footer(run, generated_at)),
    )
    return env


def render_header(run):
    started = fmt_timestamp(run.get("started_at"))
    finished = fmt_timestamp(run.get("finished_at"))
    duration = fmt_duration(run.get("duration_ms"))
    st = run.get("status", "unknown")
    run_id = esc(run.get("run_id", "N/A"))
    plan = esc(run.get("test_plan", "N/A"))
    return f"""\
<div class="card">
  <div style="display:flex;align-items:center;gap:1rem;flex-wrap:wrap;margin-bottom:1rem">
    {badge(st)}
    <h1>Run {run_id}</h1>
  </div>
  <div class="meta-grid">
    <span class="label">Test Plan</span><span class="value">{plan}</span>
    <span class="label">Started</span><span class="value">{esc(started)}</span>
    <span class="label">Finished</span><span class="value">{esc(finished)}</span>
    <span class="label">Duration</span><span class="value">{esc(duration)}</span>
  </div>
</div>"""


def render_sut(run):
    sut = run.get("sut")
    if not sut:
        return ""
    repo = sut.get("repo", "unknown")
    commit = sut.get("commit", "unknown")
    commit_short = sut.get("commit_short", commit[:7] if commit else "unknown")
    branch = sut.get("branch", "unknown")
    pr = sut.get("pr")
    backend = sut.get("backend", "unknown")
    portal = sut.get("portal", "builtin")
    version = sut.get("installed_version", "unknown")

    repo_url = gh_repo_url(repo)
    commit_url = gh_commit_url(repo, commit)
    pr_url = gh_pr_url(repo, pr)

    repo_link = f'<a href="{esc(repo_url)}">{esc(repo)}</a>' if repo_url else esc(repo)
    commit_link = (f'<a href="{esc(commit_url)}" class="mono">{esc(commit_short)}</a>'
                   if commit_url else f'<span class="mono">{esc(commit_short)}</span>')
    pr_cell = (f'<a href="{esc(pr_url)}">#{esc(pr)}</a>' if pr_url
               else (esc(str(pr)) if pr else "N/A"))

    return f"""\
<div class="card">
  <div class="section-title">System Under Test</div>
  <div class="meta-grid">
    <span class="label">Repo</span><span class="value">{repo_link}</span>
    <span class="label">Commit</span><span class="value">{commit_link}</span>
    <span class="label">Branch</span><span class="value">{esc(branch)}</span>
    <span class="label">PR</span><span class="value">{pr_cell}</span>
    <span class="label">Backend</span><span class="value">{esc(backend)}</span>
    <span class="label">Portal</span><span class="value">{esc(portal)}</span>
    <span class="label">Version</span><span class="value mono">{esc(version)}</span>
  </div>
</div>"""


def render_lab(run):
    lab = run.get("lab")
    if not lab:
        return ""
    router_id = esc(lab.get("router_id", "unknown"))
    router_model = esc(lab.get("router_model", "unknown"))
    router_arch = esc(lab.get("router_arch", "unknown"))
    client_type = esc(lab.get("client_type", "unknown"))
    viewport = esc(lab.get("viewport", "unknown"))
    virtual = "Yes" if lab.get("virtual_lab") else "No"
    return f"""\
<div class="card">
  <div class="section-title">Lab</div>
  <div class="meta-grid">
    <span class="label">Router</span><span class="value">{router_id} ({router_model})</span>
    <span class="label">Arch</span><span class="value mono">{router_arch}</span>
    <span class="label">Client</span><span class="value">{client_type}</span>
    <span class="label">Viewport</span><span class="value">{viewport}</span>
    <span class="label">Virtual Lab</span><span class="value">{virtual}</span>
  </div>
</div>"""


def render_runner_card(runner):
    name = esc(runner.get("name", "unknown"))
    fw = esc(runner.get("framework", "unknown"))
    st = runner.get("status", "unknown")
    counts = runner.get("counts", {})
    duration = fmt_duration(runner.get("duration_ms"))
    artifacts = runner.get("artifacts", {})

    total = counts.get("total", 0)
    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0)
    errors = counts.get("errors", 0)
    skipped = counts.get("skipped", 0)
    flaky = counts.get("flaky", 0)

    art_links = []
    artifact_labels = {"junit": "JUnit XML", "html": "HTML Report", "log": "Log",
                       "json": "Playwright JSON"}
    for key, label in artifact_labels.items():
        path = artifacts.get(key)
        if path:
            art_links.append(f'<a href="../{esc(path)}">{esc(label)}</a>')

    art_html = ""
    if art_links:
        art_html = f'<div class="artifacts-links">{"".join(art_links)}</div>'

    return f"""\
<div class="runner-card {esc(st)}">
  <div class="runner-name">{name} {small_badge(st)}</div>
  <div class="runner-meta">{esc(fw)} &middot; {esc(duration)}</div>
  <div class="counts-row">
    <span>Total: {total}</span>
    <span class="c-pass">Pass: {passed}</span>
    <span class="c-fail">Fail: {failed}</span>
    <span class="c-err">Error: {errors}</span>
    <span class="c-skip">Skip: {skipped}</span>
    <span class="c-flaky">Flaky: {flaky}</span>
  </div>
  {art_html}
</div>"""


def render_runners(run):
    runners = run.get("runners", [])
    if not runners:
        return '<div class="card"><div class="section-title">Runners</div><p>No runners recorded.</p></div>'
    cards = "\n".join(render_runner_card(r) for r in runners)
    return f"""\
<div class="card">
  <div class="section-title">Runners</div>
  <div class="runners-grid">
    {cards}
  </div>
</div>"""


def render_failed(run, summary):
    failed = []
    if summary:
        failed = summary.get("failed_tests", [])
    if not failed:
        return ""
    rows = []
    for t in failed:
        runner = esc(t.get("runner", ""))
        name = esc(t.get("name", ""))
        file_path = esc(t.get("file", ""))
        msg = esc(truncate(t.get("failure_message"), 200))
        rows.append(
            f'<tr><td>{runner}</td><td>{name}</td>'
            f'<td class="mono-cell">{file_path}</td>'
            f'<td>{msg}</td></tr>'
        )
    return f"""\
<div class="card">
  <div class="section-title" style="color:#d93025">Failed Tests ({len(failed)})</div>
  <table>
    <thead><tr><th>Runner</th><th>Test</th><th>File</th><th>Message</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>
</div>"""


def render_skipped(run, summary):
    skipped = []
    if summary:
        skipped = summary.get("skipped_tests", [])
    if not skipped:
        return ""
    rows = []
    for t in skipped:
        runner = esc(t.get("runner", ""))
        name = esc(t.get("name", ""))
        reason = esc(t.get("failure_message") or t.get("skip_reason") or "")
        rows.append(
            f'<tr><td>{runner}</td><td>{name}</td><td>{reason}</td></tr>'
        )
    return f"""\
<div class="card">
  <details>
    <summary>Skipped Tests ({len(skipped)})</summary>
    <table>
      <thead><tr><th>Runner</th><th>Test</th><th>Reason</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </details>
</div>"""


def render_pipeline_timing(run):
    steps = run.get("pipeline_steps")
    if not steps:
        return ""
    max_dur = max((s.get("duration_ms", 0) for s in steps), default=1) or 1
    bar_color = "#4285f4"
    bg_color = "#e8f0fe"
    rows = []
    for s in steps:
        name = esc(s.get("step", ""))
        dur_ms = s.get("duration_ms", 0)
        dur_s = f"{dur_ms / 1000:.1f}s"
        pct = round(dur_ms / max_dur * 100)
        rows.append(
            f'<tr>'
            f'<td class="mono-cell">{name}</td>'
            f'<td style="width:60%"><div style="background:{bg_color};border-radius:3px;height:18px;position:relative">'
            f'<div style="background:{bar_color};border-radius:3px;height:18px;width:{pct}%"></div></div></td>'
            f'<td style="text-align:right;white-space:nowrap;font-weight:600">{esc(dur_s)}</td>'
            f'</tr>'
        )
    return f"""\
<div class="card">
  <details>
    <summary>Pipeline Timing ({len(steps)} steps)</summary>
    <table>
      <thead><tr><th>Step</th><th></th><th style="text-align:right">Duration</th></tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </details>
</div>"""


def render_native_links(run):
    runners = run.get("runners", [])
    e2e = run.get("e2e_artifacts", {})
    has_media = e2e.get("video") or e2e.get("screenshots")
    if not runners and not has_media:
        return ""

    links = []
    for r in runners:
        artifacts = r.get("artifacts", {})
        name = r.get("name", "unknown")
        if "html" in artifacts:
            links.append(f'<li><a href="../{esc(artifacts["html"])}">{esc(name)} HTML Report</a></li>')
        if "junit" in artifacts:
            links.append(f'<li><a href="../{esc(artifacts["junit"])}">{esc(name)} JUnit XML</a></li>')
        if "json" in artifacts:
            links.append(f'<li><a href="../{esc(artifacts["json"])}">{esc(name)} Playwright JSON</a></li>')
        if "log" in artifacts:
            links.append(f'<li><a href="../{esc(artifacts["log"])}">{esc(name)} Output Log</a></li>')

    for scan_path in run.get("vwifi_scans", []):
        label = Path(scan_path).stem.replace("-", " ").title()
        links.append(f'<li><a href="../{esc(scan_path)}">📡 {esc(label)}</a></li>')

    links_html = ""
    if links:
        links_html = f'<ul class="native-links">{"".join(links)}</ul>'

    media_html = ""
    media_items = []
    if e2e.get("video"):
        vid_path = esc(e2e["video"])
        onclick = f"openLightbox('../{vid_path}','video','Portal Flow Video')"
        media_items.append(
            f'<div class="media-item video-item" onclick="{onclick}">'
            f'<video src="../{vid_path}" muted preload="metadata"></video>'
            f'<span class="media-label">Portal Flow</span></div>'
        )
    for i, ss in enumerate(e2e.get("screenshots", [])):
        label = Path(ss).stem.replace("-", " ").title() if ss else f"Screenshot {i+1}"
        img_path = esc(ss)
        onclick = f"openLightbox('../{img_path}','image','{esc(label)}')"
        media_items.append(
            f'<div class="media-item" onclick="{onclick}">'
            f'<img src="../{img_path}" alt="{esc(label)}" loading="lazy">'
            f'<span class="media-label">{esc(label)}</span></div>'
        )
    if media_items:
        media_html = f'<div class="media-gallery">{"".join(media_items)}</div>'

    if not links_html and not media_html:
        return ""
    return f"""\
<div class="card">
  <div class="section-title">Native Reports</div>
  {media_html}
  {links_html}
</div>"""


def render_footer(run, generated_at):
    run_id = esc(run.get("run_id", "N/A"))
    return f"""\
<div class="footer">
  <p>Artifacts may be sanitized for public publication. Some details may be redacted.</p>
  <p>Run {run_id} &middot; {esc(generated_at)}</p>
  <p>Generated by physical-router-test-automation</p>
</div>"""


def render_report(run, summary):
    env = build_environment()
    template = env.get_template("report.html")
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return template.render(
        run=run,
        summary=summary,
        generated_at=generated_at,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Render HTML report from run.json + summary.json")
    parser.add_argument("--run-dir", required=True, help="Path to run directory containing run.json")
    args = parser.parse_args()

    run_dir = os.path.abspath(args.run_dir)

    # Read run.json
    run_path = os.path.join(run_dir, "run.json")
    if not os.path.isfile(run_path):
        print(f"ERROR: {run_path} not found", file=sys.stderr)
        sys.exit(1)

    try:
        with open(run_path) as f:
            run = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"ERROR: Failed to read {run_path}: {e}", file=sys.stderr)
        sys.exit(1)

    # Read summary.json (optional)
    summary = None
    summary_path = os.path.join(run_dir, "summary.json")
    if os.path.isfile(summary_path):
        try:
            with open(summary_path) as f:
                summary = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass  # proceed without summary

    # Generate HTML
    html_report = render_report(run, summary)

    # Write report
    report_dir = os.path.join(run_dir, "report")
    os.makedirs(report_dir, exist_ok=True)
    report_path = os.path.join(report_dir, "index.html")
    with open(report_path, "w") as f:
        f.write(html_report)

    print(f"==> Generated report: {report_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
