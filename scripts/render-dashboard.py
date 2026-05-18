#!/usr/bin/env python3
"""Render the gh-pages dashboard from published run metadata."""

import argparse
import html
import json
import os
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape


DEFAULT_REPO = "OpenTollGate/tollgate-module-basic-go"


def get(d: Any, path: str, default: Any = "") -> Any:
    """Get nested dict value with dot notation."""
    keys = path.split(".")
    value = d
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return default
    if value is None:
        return default
    return value


def read_run(path):
    """Read run.json, supporting both new (schema_version>=1) and old (flat) schemas."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)

    sv = d.get("schema_version", 0)

    if sv >= 1:
        commit = get(d, "sut.commit", "unknown")
        commit_short = get(d, "sut.commit_short", commit[:7])
        branch = get(d, "sut.branch", "")
        pr = get(d, "sut.pr", "")
        backend = get(d, "sut.backend", "")
        repo = get(d, "sut.repo", "")
        router_id = get(d, "lab.router_id", "unknown")
        client_type = get(d, "lab.client_type", "")
        viewport = get(d, "lab.viewport", "")
        test_plan = get(d, "test_plan", "")
        status = get(d, "status", "")
        started_at = get(d, "started_at", "")
        duration_ms = get(d, "duration_ms", 0)
        counts = get(d, "counts", {})
        runners = get(d, "runners", [])
        installed_version = get(d, "sut.installed_version", "")
        build_time = get(d, "sut.build_time", "")
        openwrt_version = get(d, "sut.openwrt_version", "")
        go_version = get(d, "sut.go_version", "")
        virtual_lab = get(d, "lab.virtual_lab", False)
    else:
        commit = d.get("tollgate_commit", "unknown")
        commit_short = commit[:7]
        branch = d.get("tollgate_branch", "")
        pr = d.get("tollgate_pr", "")
        backend = ""
        repo = d.get("sut_repo", DEFAULT_REPO)
        router_id = d.get("router_id", "unknown")
        client_type = d.get("client_type", "")
        viewport = d.get("viewport", "")
        test_plan = d.get("test_type", "e2e")
        status = ""
        started_at = d.get("timestamp", "")
        duration_ms = d.get("duration_ms", 0)
        counts = {
            "total": d.get("total", 0),
            "passed": d.get("passed", 0),
            "failed": d.get("failed", 0),
            "errors": 0,
            "skipped": d.get("skipped", 0),
            "flaky": d.get("flaky", 0),
        }
        runners = []
        installed_version = ""
        build_time = ""
        openwrt_version = ""
        go_version = ""
        virtual_lab = False

    return {
        "commit": commit,
        "commit_short": commit_short,
        "branch": branch,
        "pr": pr,
        "backend": backend,
        "repo": repo,
        "router_id": router_id,
        "client_type": client_type,
        "viewport": viewport,
        "test_plan": test_plan,
        "status": status,
        "started_at": started_at,
        "duration_ms": int(duration_ms) if duration_ms else 0,
        "counts": counts,
        "runners": runners,
        "installed_version": installed_version,
        "build_time": build_time,
        "openwrt_version": openwrt_version,
        "go_version": go_version,
        "virtual_lab": virtual_lab,
    }


def format_ts(ts):
    """Format ISO timestamp to human-readable."""
    if not ts:
        return "N/A"
    for fmt in (
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H-%M-%S",
        "%Y-%m-%dT%H-%M-%SZ",
    ):
        try:
            dt = datetime.strptime(ts, fmt)
            months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            return "%s %d, %d %02d:%02d UTC" % (
                months[dt.month], dt.day, dt.year, dt.hour, dt.minute)
        except ValueError:
            continue
    return ts


def format_duration(ms):
    """Format duration in ms to human-readable."""
    if not ms or ms <= 0:
        return "-"
    seconds = ms / 1000
    if seconds < 60:
        return "%ds" % int(seconds)
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return "%dm%02ds" % (minutes, secs)


def esc(s):
    """HTML-escape a string."""
    return html.escape(str(s), quote=True)


def badge_status(run):
    status = run.get("status", "")
    if status:
        return status
    counts = run.get("counts", {})
    if counts.get("failed", 0) > 0 or counts.get("errors", 0) > 0:
        return "failed"
    return "passed"


def repo_base_url(repo):
    if repo and "/" in repo:
        return "https://github.com/%s" % repo
    return "https://github.com/%s" % DEFAULT_REPO


def collect_runs(reports_dir):
    runs = []
    if os.path.isdir(reports_dir):
        for root, _, files in os.walk(reports_dir):
            if "run.json" not in files:
                continue
            run_json = os.path.join(root, "run.json")
            rel = os.path.relpath(root, reports_dir)
            parts = rel.split(os.sep)
            if len(parts) != 2:
                continue
            hash_dir_name, ts_dir_name = parts
            try:
                run = read_run(run_json)
                run["hash_dir"] = hash_dir_name
                run["ts_dir"] = ts_dir_name
                run["report_path"] = (
                    "reports/%s/%s/report/index.html"
                    % (hash_dir_name, ts_dir_name)
                )
                run["badge_status"] = badge_status(run)
                runs.append(run)
            except Exception:
                continue

    runs.sort(key=lambda r: r.get("started_at", "") or "", reverse=True)
    return runs


def build_commit_groups(runs):
    groups = OrderedDict()
    for run in runs:
        commit = run.get("commit", "unknown")
        if commit not in groups:
            meta = run
            repo = meta.get("repo", "") or DEFAULT_REPO
            branch = meta.get("branch", "")
            pr = meta.get("pr", "")
            base_url = repo_base_url(repo)
            groups[commit] = {
                "commit": commit,
                "short": meta.get("commit_short", commit[:7]),
                "branch": branch,
                "pr": pr if str(pr) not in ("0", "") else "",
                "commit_url": "%s/commit/%s" % (base_url, commit),
                "branch_url": "%s/tree/%s" % (base_url, branch) if branch else "",
                "pr_url": "%s/pull/%s" % (base_url, pr) if str(pr) not in ("0", "") else "",
                "version": meta.get("installed_version", ""),
                "build_time": meta.get("build_time", ""),
                "openwrt_version": meta.get("openwrt_version", ""),
                "virtual_lab": meta.get("virtual_lab", False),
                "runs": [],
            }
        groups[commit]["runs"].append(run)
    return list(groups.values())


def build_environment(templates_dir):
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "htm"]),
        variable_start_string="[[",
        variable_end_string="]]",
        block_start_string="[%",
        block_end_string="%]",
        comment_start_string="[#",
        comment_end_string="#]",
    )
    env.filters["format_ts"] = format_ts
    env.filters["format_duration"] = format_duration
    env.filters["esc"] = esc
    env.globals["get"] = get
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", required=True,
                        help="Path to gh-pages/reports directory")
    parser.add_argument("--output", required=True,
                        help="Path to write dashboard index.html")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    templates_dir = script_dir.parent / "templates"
    env = build_environment(templates_dir)
    template = env.get_template("dashboard.html")

    runs = collect_runs(args.reports_dir)
    commit_groups = build_commit_groups(runs)
    total_runs = len(runs)
    total_commits = len(commit_groups)
    last_updated = format_ts(runs[0]["started_at"]) if runs else "N/A"

    rendered = template.render(
        last_updated=last_updated,
        total_runs=total_runs,
        total_commits=total_commits,
        commit_groups=commit_groups,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered + "\n", encoding="utf-8")

    print("==> Dashboard written to %s (%d runs, %d commits)"
          % (args.output, total_runs, total_commits))


if __name__ == "__main__":
    main()
