#!/usr/bin/env python3
"""Render the gh-pages dashboard from published run metadata."""

import argparse
import html
import json
import os
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from jinja2 import Environment, FileSystemLoader, select_autoescape


DEFAULT_REPO = "OpenTollGate/tollgate-module-basic-go"

LAB_ORDER = ["virtual-lab", "gcloud", "physical-phone", "physical-mac", "physical-linux", "physical", "unknown"]
TIER_ORDER = ["api", "captive-portal", "luci-ui"]

LAB_DISPLAY = {
    "virtual-lab": "Virtual Lab",
    "gcloud": "GCloud",
    "physical-phone": "Physical (Phone)",
    "physical-mac": "Physical (Mac)",
    "physical-linux": "Physical (Linux)",
    "physical": "Physical",
    "unknown": "Unknown",
}

LAB_DOT_COLOR = {
    "virtual-lab": "#16a34a",
    "gcloud": "#ea580c",
    "physical-phone": "#2563eb",
    "physical-mac": "#2563eb",
    "physical-linux": "#2563eb",
    "physical": "#2563eb",
    "unknown": "#6b7280",
}


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


def is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def derive_lab_type(data: dict[str, Any], schema_version: int) -> str:
    if schema_version >= 1:
        lab_type = get(data, "lab.type", "")
        if lab_type:
            return str(lab_type)
        return "virtual-lab" if is_truthy(get(data, "lab.virtual_lab", False)) else "physical"
    return "virtual-lab" if is_truthy(data.get("virtual_lab", False)) else "physical"


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

    lab_type = derive_lab_type(d, sv)

    tier = get(d, "tier", "")
    scope = get(d, "scope", "")
    if not tier:
        tp = test_plan.lower()
        if tp.startswith("api"):
            tier = "api"
        elif tp.startswith("captive") or tp.startswith("phone"):
            tier = "captive-portal"
        elif tp.startswith("luci"):
            tier = "luci-ui"
        elif tp == "e2e":
            tier = "api"

    matrix_lab = lab_type
    if lab_type == "physical":
        ct = client_type.lower() if client_type else ""
        if ct == "adb":
            matrix_lab = "physical-phone"
        elif ct == "mac":
            matrix_lab = "physical-mac"
        elif ct == "linux":
            matrix_lab = "physical-linux"

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
        "lab_type": lab_type,
        "matrix_lab": matrix_lab,
        "tier": tier,
        "scope": scope,
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
    if repo and repo != "unknown" and "/" in repo:
        return f"https://github.com/{repo}"
    return ""


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
                    f"reports/{hash_dir_name}/{ts_dir_name}/report/index.html"
                )
                run["badge_status"] = badge_status(run)
                runs.append(run)
            except Exception:
                continue

    runs.sort(key=lambda r: r.get("started_at", "") or "", reverse=True)
    return runs


def parse_version(version_str):
    """Parse installed_version into structured components.

    Examples:
        feature-v2-keyset-support.98.f89e109 -> prefix='feature-v2-keyset-support', run=98
        126-merge.99.161d30b -> prefix='126-merge', run=99
        main.42.abc1234 -> prefix='main', run=42
    """
    if not version_str or version_str == "unknown":
        return {"raw": version_str or "", "display": version_str or "", "run_number": "", "ci_url": ""}

    parts = version_str.split(".")
    prefix = parts[0] if parts else ""
    run_number = parts[1] if len(parts) > 1 else ""
    commit_part = parts[2] if len(parts) > 2 else ""

    display = version_str
    if len(parts) == 3:
        show_prefix = prefix if len(prefix) <= 28 else prefix[:25] + "..."
        display = f"{show_prefix} · #{run_number}"

    return {
        "raw": version_str,
        "display": display,
        "prefix": prefix,
        "run_number": run_number,
        "commit_part": commit_part,
    }


def build_commit_groups(runs):
    # Phase 1: Build initial groups by commit hash (same as before)
    commit_to_runs = OrderedDict()
    for run in runs:
        commit = run.get("commit", "unknown")
        branch = run.get("branch", "")
        pr = run.get("pr", "")
        group_key = commit
        if commit == "unknown":
            fallback = ""
            if str(pr) not in ("0", "", "None"):
                fallback = f"pr-{pr}"
            elif branch and branch != "unknown":
                fallback = f"branch-{branch}"
            group_key = "unknown:%s" % (fallback or "metadata")
        if group_key not in commit_to_runs:
            commit_to_runs[group_key] = []
        commit_to_runs[group_key].append(run)

    # Phase 2: Merge groups that share the same PR number
    pr_groups = OrderedDict()
    non_pr_groups = []

    for group_key, group_runs in commit_to_runs.items():
        pr = ""
        for r in group_runs:
            p = r.get("pr", "")
            if str(p) not in ("0", "", "None"):
                pr = str(p)
                break
        if pr:
            pr_key = f"pr-{pr}"
            if pr_key not in pr_groups:
                pr_groups[pr_key] = []
            pr_groups[pr_key].append(group_key)
        else:
            non_pr_groups.append(group_key)

    merged = OrderedDict()
    for pr_key, old_keys in pr_groups.items():
        merged[pr_key] = old_keys
    for gk in non_pr_groups:
        merged[gk] = [gk]

    # Phase 3: Build final groups from merged mapping
    groups = OrderedDict()
    for new_key, old_keys in merged.items():
        all_runs = []
        for ok in old_keys:
            all_runs.extend(commit_to_runs[ok])

        all_runs.sort(key=lambda r: (
            0 if r.get("started_at", "") else 1,
            r.get("started_at", ""),
        ), reverse=True)

        meta = all_runs[0] if all_runs else {}

        # For PR groups, prefer feature branch metadata over merge commit
        feature_meta = meta
        for r in all_runs:
            b = r.get("branch", "")
            if b and b not in ("main", "master"):
                feature_meta = r
                break

        repo = feature_meta.get("repo", "") or DEFAULT_REPO
        if repo == "unknown":
            repo = ""
        branch = feature_meta.get("branch", "")
        pr = feature_meta.get("pr", "")
        commit = feature_meta.get("commit", "unknown")
        base_url = repo_base_url(repo)
        short = feature_meta.get("commit_short", commit[:7])

        # Collect all unique commits in this group
        all_commits = []
        seen_commits = set()
        for r in all_runs:
            c = r.get("commit", "unknown")
            cs = r.get("commit_short", c[:7])
            if c not in seen_commits:
                seen_commits.add(c)
                all_commits.append({"hash": c, "short": cs})

        if commit == "unknown" and branch and branch != "unknown":
            short = branch

        version_str = feature_meta.get("installed_version", "")
        version_info = parse_version(version_str)

        groups[new_key] = {
            "commit": commit,
            "short": short,
            "branch": branch,
            "pr": pr if str(pr) not in ("0", "") else "",
            "commit_url": f"{base_url}/commit/{commit}" if base_url else "",
            "branch_url": f"{base_url}/tree/{branch}" if base_url and branch else "",
            "pr_url": f"{base_url}/pull/{pr}" if base_url and str(pr) not in ("0", "") else "",
            "version": version_str,
            "version_info": version_info,
            "build_time": feature_meta.get("build_time", ""),
            "openwrt_version": feature_meta.get("openwrt_version", ""),
            "virtual_lab": feature_meta.get("virtual_lab", False),
            "runs": all_runs,
            "all_commits": all_commits,
            "_is_pr_group": new_key.startswith("pr-"),
        }

    # Phase 4: Build matrix per group
    commit_groups = []
    for new_key, group in groups.items():
        lab_type_set = set()
        for run in group["runs"]:
            lab_type_set.add(run.get("lab_type", "") or "unknown")
        lab_types = sorted(lab_type_set)
        group["lab_types"] = lab_types
        if len(lab_types) > 1:
            group["lab_type"] = "mixed"
        elif lab_types:
            group["lab_type"] = lab_types[0]
        else:
            group["lab_type"] = "unknown"

        matrix = {}
        seen_tiers = set()
        seen_labs = set()
        for run in group["runs"]:
            lab = run.get("matrix_lab", "unknown")
            tier = run.get("tier", "")
            if not tier:
                continue
            scope = run.get("scope", "")
            key = f"{lab}|{tier}"
            seen_tiers.add(tier)
            seen_labs.add(lab)
            existing = matrix.get(key)
            if not existing:
                matrix[key] = run
            elif scope == "full" and existing.get("scope") == "quick":
                matrix[key] = run
            elif scope == existing.get("scope") and run.get("started_at", "") > existing.get("started_at", ""):
                matrix[key] = run

        ordered_tiers = [t for t in TIER_ORDER if t in seen_tiers]
        ordered_tiers.extend(sorted(seen_tiers - set(TIER_ORDER)))
        ordered_labs = [l for l in LAB_ORDER if l in seen_labs]
        ordered_labs.extend(sorted(seen_labs - set(LAB_ORDER)))

        # Compute aggregate stats
        total_passed = sum(r.get("counts", {}).get("passed", 0) for r in group["runs"])
        total_failed = sum(r.get("counts", {}).get("failed", 0) + r.get("counts", {}).get("errors", 0) for r in group["runs"])
        total_skipped = sum(r.get("counts", {}).get("skipped", 0) for r in group["runs"])

        latest_ts = ""
        for r in group["runs"]:
            ts = r.get("started_at", "")
            if ts and ts > latest_ts:
                latest_ts = ts

        group["matrix"] = matrix
        group["tiers"] = ordered_tiers
        group["labs"] = ordered_labs
        group["stats"] = {
            "passed": total_passed,
            "failed": total_failed,
            "skipped": total_skipped,
            "any_failed": total_failed > 0,
        }
        group["_latest_ts"] = latest_ts

        commit_groups.append(group)

    # Phase 5: Sort groups — PR groups first (by latest run desc), then non-PR groups
    def sort_key(g):
        is_pr = 0 if g.get("_is_pr_group") else 1
        ts = g.get("_latest_ts", "") or ""
        return (is_pr, ts)

    commit_groups.sort(key=sort_key)

    # Clean up internal keys
    for g in commit_groups:
        g.pop("_is_pr_group", None)
        g.pop("_latest_ts", None)

    return commit_groups


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
    cast(dict[str, Any], env.globals)["get"] = get
    cast(dict[str, Any], env.globals)["matrix_get"] = lambda m, l, t: m.get(f"{l}|{t}")
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
    last_updated = format_ts(runs[0]["started_at"]) if runs and runs[0].get("started_at") else ""
    if not last_updated and runs:
        ts_dir = runs[0].get("ts_dir", "")
        if ts_dir and len(ts_dir) >= 15:
            last_updated = format_ts(ts_dir[:15].replace("T", "T"))
    if not last_updated:
        last_updated = "N/A"

    unique_prs = sorted(set(
        str(r.get("pr", ""))
        for r in runs
        if str(r.get("pr", "")) not in ("0", "", "None")
    ))
    unique_branches = sorted(set(
        r.get("branch", "")
        for r in runs
        if r.get("branch", "") and r.get("branch", "") != "unknown"
    ))
    unique_statuses = sorted(set(
        badge_status(r)
        for r in runs
    ))

    rendered = template.render(
        last_updated=last_updated,
        total_runs=total_runs,
        total_commits=total_commits,
        commit_groups=commit_groups,
        lab_display=LAB_DISPLAY,
        lab_dot_color=LAB_DOT_COLOR,
        filter_prs=unique_prs,
        filter_branches=unique_branches,
        filter_statuses=unique_statuses,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered + "\n", encoding="utf-8")

    print("==> Dashboard written to %s (%d runs, %d commits)"
          % (args.output, total_runs, total_commits))


if __name__ == "__main__":
    main()
