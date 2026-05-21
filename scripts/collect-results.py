#!/usr/bin/env python3
"""Collect test results from JUnit XML and Playwright JSON into canonical run.json + summary.json."""

import argparse
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone


def parse_junit(xml_path, runner_name):
    """Parse a JUnit XML file and return a runner dict + list of test dicts."""
    tests = []
    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Handle <testsuites> wrapper or direct <testsuite>
    ts = root if root.tag == "testsuite" else root.find("testsuite")
    if ts is None:
        ts = root

    ts_time = float(ts.get("time", "0"))
    total = int(ts.get("tests", "0"))
    failures = int(ts.get("failures", "0"))
    errors = int(ts.get("errors", "0"))
    skipped = int(ts.get("skipped", "0"))
    passed = max(0, total - failures - errors - skipped)

    for tc in ts.iter("testcase"):
        name = tc.get("name", "unknown")
        classname = tc.get("classname", "")
        file_path = classname.replace(".", os.sep) if classname else ""
        duration = round(float(tc.get("time", "0")) * 1000)

        failure_el = tc.find("failure")
        error_el = tc.find("error")
        skip_el = tc.find("skipped")

        if failure_el is not None:
            outcome = "failed"
            failure_message = failure_el.get("message") or (failure_el.text or "").strip()
        elif error_el is not None:
            outcome = "error"
            failure_message = error_el.get("message") or (error_el.text or "").strip()
        elif skip_el is not None:
            outcome = "skipped"
            failure_message = skip_el.get("message") or (skip_el.text or "").strip()
        else:
            outcome = "passed"
            failure_message = None

        tests.append({
            "runner": runner_name,
            "framework": "pytest",
            "name": name,
            "file": file_path,
            "outcome": outcome,
            "duration_ms": duration,
            "failure_message": failure_message,
            "markers": [],
        })

    runner_status = "passed"
    if errors > 0:
        runner_status = "errored"
    if failures > 0:
        runner_status = "failed"

    runner = {
        "name": runner_name,
        "framework": "pytest",
        "status": runner_status,
        "duration_ms": round(ts_time * 1000),
        "counts": {
            "total": total,
            "passed": passed,
            "failed": failures,
            "errors": errors,
            "skipped": skipped,
            "flaky": 0,
        },
    }
    return runner, tests


def parse_pytest_log(log_path, runner_name):
    """Parse pytest verbose output.log as fallback when JUnit XML is missing/incomplete.

    Extracts test results from lines like:
        path/test_foo.py::test_bar PASSED                            [ 27%]
        path/test_foo.py::test_baz FAILED                            [ 50%]
        path/test_foo.py::test_qux SKIPPED (reason here)
    """
    with open(log_path) as f:
        content = f.read()

    # Match: file::name STATUS [percentage]
    # Status can be PASSED, FAILED, SKIPPED, ERROR, XFAIL, XPASS
    pattern = re.compile(
        r'^([\w./_\-]+\.py)::(\S+)\s+'
        r'(PASSED|FAILED|SKIPPED|ERROR|XFAIL|XPASS)'
        r'(?:\s|\()',
        re.MULTILINE,
    )

    tests = []
    counts = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "flaky": 0}

    for match in pattern.finditer(content):
        file_path = match.group(1)
        name = match.group(2)
        status_raw = match.group(3)

        # Map pytest statuses to our outcome types
        outcome_map = {
            "PASSED": "passed",
            "FAILED": "failed",
            "ERROR": "error",
            "SKIPPED": "skipped",
            "XFAIL": "skipped",
            "XPASS": "passed",
        }
        outcome = outcome_map.get(status_raw, "passed")

        counts["total"] += 1
        if outcome == "passed":
            counts["passed"] += 1
        elif outcome == "failed":
            counts["failed"] += 1
        elif outcome == "error":
            counts["errors"] += 1
        elif outcome == "skipped":
            counts["skipped"] += 1

        tests.append({
            "runner": runner_name,
            "framework": "pytest",
            "name": name,
            "file": file_path,
            "outcome": outcome,
            "duration_ms": 0,
            "failure_message": None,
            "markers": [],
        })

    runner_status = "passed"
    if counts["errors"] > 0:
        runner_status = "errored"
    if counts["failed"] > 0:
        runner_status = "failed"

    duration_ms = 0
    dur_match = re.search(r'in\s+([\d.]+)s', content)
    if dur_match:
        duration_ms = round(float(dur_match.group(1)) * 1000)

    per_test_ms = round(duration_ms / max(counts["total"], 1)) if duration_ms > 0 else 0
    for t in tests:
        t["duration_ms"] = per_test_ms

    runner = {
        "name": runner_name,
        "framework": "pytest",
        "status": runner_status,
        "duration_ms": duration_ms,
        "counts": counts,
    }
    return runner, tests


def _collect_playwright_specs(suites, runner_name):
    """Recursively collect specs from Playwright suite hierarchy."""
    tests = []
    for suite in suites:
        child_suites = suite.get("suites", [])
        tests.extend(_collect_playwright_specs(child_suites, runner_name))

        for spec in suite.get("specs", []):
            title = spec.get("title", "unknown")
            file_path = spec.get("file", "")

            for test_entry in spec.get("tests", []):
                results = test_entry.get("results", [])
                if not results:
                    continue

                pw_status = test_entry.get("status", "skipped")
                first_result = results[0]

                status_map = {
                    "expected": "passed",
                    "unexpected": "failed",
                    "flaky": "flaky",
                    "skipped": "skipped",
                }
                outcome = status_map.get(pw_status, "passed")

                duration = round(first_result.get("duration", 0))

                failure_message = None
                if outcome in ("failed", "flaky"):
                    err = first_result.get("error")
                    if err and isinstance(err, dict):
                        failure_message = err.get("message")

                tests.append({
                    "runner": runner_name,
                    "framework": "playwright",
                    "name": title,
                    "file": file_path,
                    "outcome": outcome,
                    "duration_ms": duration,
                    "failure_message": failure_message,
                    "markers": [],
                })
    return tests


def parse_playwright(json_path, runner_name):
    """Parse a Playwright JSON results file and return a runner dict + list of test dicts."""
    with open(json_path) as f:
        data = json.load(f)

    stats = data.get("stats", {})
    passed = int(stats.get("expected", 0))
    failed = int(stats.get("unexpected", 0))
    flaky = int(stats.get("flaky", 0))
    skipped = int(stats.get("skipped", 0))
    total = passed + failed + flaky + skipped
    duration_ms = round(stats.get("duration", 0))

    runner_status = "passed"
    if failed > 0:
        runner_status = "failed"
    elif flaky > 0:
        runner_status = "passed"

    runner = {
        "name": runner_name,
        "framework": "playwright",
        "status": runner_status,
        "duration_ms": duration_ms,
        "counts": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": 0,
            "skipped": skipped,
            "flaky": flaky,
        },
    }

    tests = _collect_playwright_specs(data.get("suites", []), runner_name)
    return runner, tests


def determine_overall_status(runners):
    """Determine overall status from a list of runner dicts."""
    if not runners:
        return "errored"

    all_passed = all(r["status"] == "passed" for r in runners)
    if all_passed:
        return "passed"

    has_failures = any(
        r["counts"]["failed"] > 0 or r["counts"]["errors"] > 0
        for r in runners
    )
    if has_failures:
        return "failed"

    return "partial"


def merge_counts(runners):
    """Merge counts from all runners."""
    merged = {"total": 0, "passed": 0, "failed": 0, "errors": 0, "skipped": 0, "flaky": 0}
    for r in runners:
        for key in merged:
            merged[key] += r["counts"].get(key, 0)
    return merged


def _resolve_commit_from_branch(repo, branch):
    """Resolve a branch/ref to a commit SHA via gh. Returns None on failure."""
    if not repo or repo == "unknown" or not branch or branch == "unknown":
        return None
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/commits/{branch}", "--jq", ".sha"],
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return None


def _query_router_version(router_ip):
    """SSH to router and query tollgate version socket. Returns dict or None."""
    ssh_user = os.environ.get("TOLLGATE_SSH_USER", "root")
    ssh_password = os.environ.get("TOLLGATE_SSH_PASSWORD")
    ssh_key = os.environ.get("TOLLGATE_SSH_KEY")
    jump_host = os.environ.get("TOLLGATE_SSH_JUMP_HOST")

    ssh_opts = ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR"]
    # BatchMode=yes prevents password auth; only use it when sshpass is NOT providing the password
    if not ssh_password:
        ssh_opts += ["-o", "BatchMode=yes"]

    cmd = ["ssh"] + ssh_opts

    if jump_host:
        cmd += ["-J", jump_host]
    if ssh_key and os.path.isfile(ssh_key):
        cmd += ["-i", ssh_key]

    cmd.append(f"{ssh_user}@{router_ip}")

    remote_cmd = """echo '{"command": "version"}' | socat - UNIX-CONNECT:/var/run/tollgate.sock"""
    cmd.append(remote_cmd)

    if ssh_password:
        cmd = ["sshpass", "-p", ssh_password] + cmd

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            print(f"WARNING: SSH version query failed (exit {result.returncode}): {result.stderr.strip()}", file=sys.stderr)
            return None
        output = result.stdout.strip()
        if not output:
            print("WARNING: SSH version query returned empty output", file=sys.stderr)
            return None
        resp = json.loads(output)
        if not resp.get("success"):
            print(f"WARNING: version query not successful: {resp.get('message', '')}", file=sys.stderr)
            return None
        message = resp.get("message", "")
        info = {}
        for line in message.split("\n"):
            if ": " in line:
                key, _, value = line.partition(": ")
                info[key.strip()] = value.strip()
        return {
            "commit": info.get("commit"),
            "version": info.get("version"),
            "build_time": info.get("build_time"),
            "openwrt_version": info.get("openwrt_version"),
            "go_version": info.get("go_version"),
        }
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as e:
        print(f"WARNING: failed to query router version: {e}", file=sys.stderr)
        return None


def main():
    parser = argparse.ArgumentParser(description="Collect test results into canonical JSON")
    parser.add_argument("--run-dir", required=True, help="Root directory for this test run")
    parser.add_argument("--pytest", action="append", default=[], metavar="NAME=PATH",
                        help="JUnit XML source: runner_name=path/to/junit.xml")
    parser.add_argument("--playwright", action="append", default=[], metavar="NAME=PATH",
                        help="Playwright JSON source: runner_name=path/to/results.json")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--test-plan", default=None)
    parser.add_argument("--sut-repo", default=None)
    parser.add_argument("--sut-commit", default=None)
    parser.add_argument("--sut-branch", default=None)
    parser.add_argument("--sut-pr", type=int, default=None)
    parser.add_argument("--sut-backend", default=None)
    parser.add_argument("--sut-version", default="unknown")
    parser.add_argument("--suite-commit", default=None)
    parser.add_argument("--router-id", default=None)
    parser.add_argument("--router-model", default=None)
    parser.add_argument("--router-arch", default=None)
    parser.add_argument("--router-ip", default=None)
    parser.add_argument("--client-type", default=None)
    parser.add_argument("--viewport", default=None)
    parser.add_argument("--query-router", default=None, metavar="IP",
                        help="SSH to router and query tollgate version socket to populate SUT metadata")
    parser.add_argument("--virtual-lab", action="store_true", default=False)
    parser.add_argument("--allow-failures", action="store_true", default=False)
    parser.add_argument("--started-at", default=None)
    parser.add_argument("--finished-at", default=None)
    args = parser.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    os.makedirs(run_dir, exist_ok=True)

    run_id = args.run_id
    if not run_id:
        now = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        sut_commit_raw = args.sut_commit
        if sut_commit_raw:
            sha = sut_commit_raw[:7]
        else:
            try:
                sha = subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    stderr=subprocess.DEVNULL,
                ).decode().strip()
            except Exception:
                sha = "unknown"
        run_id = f"{now}-{sha}"

    suite_commit = args.suite_commit
    if not suite_commit:
        try:
            suite_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
            ).decode().strip()
        except Exception:
            suite_commit = "unknown"

    runners = []
    all_tests = []
    parse_errors = []

    for entry in args.pytest:
        if "=" not in entry:
            print(f"ERROR: --pytest requires NAME=PATH format, got: {entry}", file=sys.stderr)
            sys.exit(2)
        name, raw_path = entry.split("=", 1)
        # Ensure path is relative to run_dir (accepts both relative and absolute)
        full_path = os.path.join(run_dir, raw_path) if not os.path.isabs(raw_path) else raw_path
        rel_path = os.path.relpath(full_path, run_dir)
        base_dir = os.path.dirname(rel_path)
        log_rel = os.path.join(base_dir, "output.log") if base_dir else "output.log"
        log_full = os.path.join(run_dir, log_rel)

        parsed = False
        if os.path.isfile(full_path):
            try:
                runner, tests = parse_junit(full_path, name)
                artifacts = {"junit": rel_path}
                html_rel = os.path.join(base_dir, "report.html") if base_dir else "report.html"
                if os.path.isfile(os.path.join(run_dir, html_rel)):
                    artifacts["html"] = html_rel
                if os.path.isfile(log_full):
                    artifacts["log"] = log_rel
                runner["artifacts"] = artifacts
                runners.append(runner)
                all_tests.extend(tests)
                parsed = True
            except Exception as e:
                parse_errors.append(f"Error parsing JUnit {name}: {e}")

        if not parsed and os.path.isfile(log_full):
            try:
                runner, tests = parse_pytest_log(log_full, name)
                artifacts = {}
                if os.path.isfile(log_full):
                    artifacts["log"] = log_rel
                runner["artifacts"] = artifacts
                runners.append(runner)
                all_tests.extend(tests)
                counts = runner.get("counts", {})
                total = counts.get("total", 0) if isinstance(counts, dict) else 0
                parse_errors.append(f"Fallback: parsed {name} from output.log ({total} tests)")
            except Exception as e:
                parse_errors.append(f"Error parsing output.log for {name}: {e}")
        elif not parsed:
            parse_errors.append(f"Missing JUnit XML: {full_path}")

    for entry in args.playwright:
        if "=" not in entry:
            print(f"ERROR: --playwright requires NAME=PATH format, got: {entry}", file=sys.stderr)
            sys.exit(2)
        name, raw_path = entry.split("=", 1)
        full_path = os.path.join(run_dir, raw_path) if not os.path.isabs(raw_path) else raw_path
        rel_path = os.path.relpath(full_path, run_dir)
        if not os.path.isfile(full_path):
            parse_errors.append(f"Missing Playwright JSON: {full_path}")
            continue
        try:
            runner, tests = parse_playwright(full_path, name)
            base_dir = os.path.dirname(rel_path)
            artifacts = {"json": rel_path}
            html_rel = os.path.join(base_dir, "report.html") if base_dir else "report.html"
            log_rel = os.path.join(base_dir, "output.log") if base_dir else "output.log"
            if os.path.isfile(os.path.join(run_dir, html_rel)):
                artifacts["html"] = html_rel
            if os.path.isfile(os.path.join(run_dir, log_rel)):
                artifacts["log"] = log_rel
            runner["artifacts"] = artifacts
            runners.append(runner)
            all_tests.extend(tests)
        except Exception as e:
            parse_errors.append(f"Error parsing Playwright {name}: {e}")

    for err in parse_errors:
        print(f"WARNING: {err}", file=sys.stderr)

    overall_status = determine_overall_status(runners)
    counts = merge_counts(runners)
    duration_ms = sum(r["duration_ms"] for r in runners)

    sut_commit = args.sut_commit or _resolve_commit_from_branch(args.sut_repo, args.sut_branch) or "unknown"
    sut = {
        "repo": args.sut_repo or "unknown",
        "commit": sut_commit,
        "commit_short": sut_commit[:7],
        "branch": args.sut_branch or "unknown",
        "pr": args.sut_pr,
        "backend": args.sut_backend or "unknown",
        "installed_version": args.sut_version or "unknown",
    }

    if args.query_router:
        version_info = _query_router_version(args.query_router)
        if version_info:
            commit = version_info.get("commit")
            if commit:
                sut["commit"] = commit
                sut["commit_short"] = commit[:7]
            if version_info.get("version"):
                sut["installed_version"] = version_info["version"]
            for field in ("build_time", "openwrt_version", "go_version"):
                if version_info.get(field):
                    sut[field] = version_info[field]

    test_suite = {
        "repo": "OpenTollGate/physical-router-test-automation",
        "commit": suite_commit,
    }

    lab = {
        "router_id": args.router_id or "unknown",
        "router_model": args.router_model or "unknown",
        "router_arch": args.router_arch or "unknown",
        "router_ip": args.router_ip if args.router_ip else "<REDACTED>",
        "client_type": args.client_type or "unknown",
        "viewport": args.viewport or "unknown",
        "virtual_lab": args.virtual_lab,
    }

    run_json = {
        "schema_version": 1,
        "run_id": run_id,
        "status": overall_status,
        "started_at": args.started_at,
        "finished_at": args.finished_at,
        "duration_ms": duration_ms,
        "test_plan": args.test_plan,
        "sut": sut,
        "test_suite": test_suite,
        "lab": lab,
        "counts": counts,
        "runners": runners,
    }

    e2e_artifacts = {"screenshots": [], "video": None}
    for media_dir in ("visual", "e2e"):
        full_media_dir = os.path.join(run_dir, "raw", media_dir)
        if not os.path.isdir(full_media_dir):
            continue
        for name in sorted(os.listdir(full_media_dir)):
            rel = os.path.join("raw", media_dir, name)
            if name.endswith(".webm") and not e2e_artifacts["video"]:
                e2e_artifacts["video"] = rel
            elif name.endswith(".png"):
                e2e_artifacts["screenshots"].append(rel)
    if e2e_artifacts["video"] or e2e_artifacts["screenshots"]:
        run_json["e2e_artifacts"] = e2e_artifacts

    failed_tests = [t for t in all_tests if t["outcome"] in ("failed", "error")]
    skipped_tests = [t for t in all_tests if t["outcome"] == "skipped"]

    summary_json = {
        "run_id": run_id,
        "status": overall_status,
        "counts": counts,
        "runners": [
            {"name": r["name"], "status": r["status"], "counts": r["counts"]}
            for r in runners
        ],
        "tests": all_tests,
        "failed_tests": failed_tests,
        "skipped_tests": skipped_tests,
    }

    run_json_path = os.path.join(run_dir, "run.json")
    with open(run_json_path, "w") as f:
        json.dump(run_json, f, indent=2)
        f.write("\n")

    summary_json_path = os.path.join(run_dir, "summary.json")
    with open(summary_json_path, "w") as f:
        json.dump(summary_json, f, indent=2)
        f.write("\n")

    runner_count = len(runners)
    print(
        f"==> Collected {runner_count} runners: {overall_status.upper()} "
        f"({counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped)",
        file=sys.stderr,
    )

    if not runners:
        sys.exit(2)

    if overall_status == "failed" and not args.allow_failures:
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
