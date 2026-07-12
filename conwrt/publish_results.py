#!/usr/bin/env python3
"""Publish conwrt test artifacts to Nostr/Blossom.

Wraps the physical-router-test-automation result_publisher to publish
conwrt test runs with project_tag=conwrt so they appear on tests.tollgate.me.

Usage:
    # After running bufferbloat test:
    python3 conwrt/publish_results.py \
        --results-dir results/bufferbloat-20260701 \
        --run-id conwrt-bufferbloat-20260701 \
        --nsec-file ~/.config/prta/nsec

The results will appear at tests.tollgate.me filtered by project=conwrt.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE_ROOT))

os.environ.setdefault("PROJECT_TAG", "conwrt")

from lib.result_publisher import publish_results


def _parse_junit_xml(xml_path: Path) -> list[dict]:
    """Parse a JUnit XML file into summary.json test entries."""
    tests: list[dict] = []
    try:
        root = ET.parse(str(xml_path)).getroot()
    except ET.ParseError:
        return tests

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    for ts in suites:
        runner_name = ts.get("name", xml_path.stem)
        for tc in ts.iter("testcase"):
            name = tc.get("name", "unknown")
            if tc.find("failure") is not None:
                outcome = "failed"
            elif tc.find("error") is not None:
                outcome = "error"
            elif tc.find("skipped") is not None:
                outcome = "skipped"
            else:
                outcome = "passed"
            tests.append({"name": name, "outcome": outcome, "runner": runner_name})
    return tests


def _generate_summary_json(
    results_dir: Path,
    passed: int | None,
    failed: int | None,
    skipped: int | None,
    errors: int | None,
) -> None:
    """Generate summary.json in results_dir for nostr-publish to upload.

    Tries JUnit XML in the results directory first; falls back to CLI counts.
    Skips generation if summary.json already exists.
    """
    summary_path = results_dir / "summary.json"
    if summary_path.exists():
        return

    tests: list[dict] = []

    for xml_path in sorted(results_dir.rglob("*.xml")):
        try:
            root = ET.parse(str(xml_path)).getroot()
        except ET.ParseError:
            continue
        if root.tag in ("testsuite", "testsuites"):
            tests.extend(_parse_junit_xml(xml_path))

    if tests:
        c_passed = sum(1 for t in tests if t["outcome"] == "passed")
        c_failed = sum(1 for t in tests if t["outcome"] == "failed")
        c_skipped = sum(1 for t in tests if t["outcome"] == "skipped")
        c_errors = sum(1 for t in tests if t["outcome"] == "error")
    else:
        c_passed = passed or 0
        c_failed = failed or 0
        c_skipped = skipped or 0
        c_errors = errors or 0

    total = c_passed + c_failed + c_skipped + c_errors
    counts: dict = {
        "passed": c_passed,
        "failed": c_failed,
        "skipped": c_skipped,
        "total": total,
    }
    if c_errors:
        counts["errors"] = c_errors

    summary = {"tests": tests, "counts": counts}
    summary_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"  Generated summary.json: {c_passed}p {c_failed}f {c_skipped}s")


def main():
    parser = argparse.ArgumentParser(description="Publish conwrt test results to Nostr/Blossom")
    parser.add_argument("--results-dir", required=True, help="Directory with test artifacts")
    parser.add_argument("--run-id", required=True, help="Unique run identifier")
    parser.add_argument("--nsec-file", default=os.environ.get("NSEC_FILE", "~/.config/prta/nsec"))
    parser.add_argument("--blossom-server", default=os.environ.get("BLOSSOM_SERVER", "https://blossom.psbt.me"))
    parser.add_argument("--relays", default=os.environ.get("NOSTR_RELAYS", "wss://relay.cashu.email"))
    parser.add_argument("--summary", default="", help="Human-readable summary for the event")
    parser.add_argument("--passed", type=int, default=None, help="Number of passed tests")
    parser.add_argument("--failed", type=int, default=None, help="Number of failed tests")
    parser.add_argument("--skipped", type=int, default=None, help="Number of skipped tests")
    parser.add_argument("--errors", type=int, default=None, help="Number of errored tests")
    parser.add_argument("--openwrt-version", default=os.environ.get("OPENWRT_VERSION", ""),
                        help="OpenWrt version tag: 24, 25, or snapshot")
    parser.add_argument("--router", default=os.environ.get("ROUTER_MODEL", ""),
                        help="Router model ID (e.g. dlink-covr-x1860-a1)")
    parser.add_argument("--use-case", default=os.environ.get("USE_CASE", ""),
                        help="Single use case name (for per-use-case runs)")
    args = parser.parse_args()

    results_dir = Path(args.results_dir).expanduser()
    if not results_dir.exists():
        print(f"ERROR: Results directory not found: {results_dir}", file=sys.stderr)
        sys.exit(1)

    nsec = Path(args.nsec_file).expanduser()
    if not nsec.exists():
        print(f"ERROR: nsec file not found: {nsec}", file=sys.stderr)
        print("Create one with: nak key generate", file=sys.stderr)
        sys.exit(1)

    summary_file = results_dir / "summary.md"
    if not args.summary and summary_file.exists():
        args.summary = summary_file.read_text()[:500]

    metadata = {
        "project": "conwrt",
        "summary": args.summary,
        "runner": "github-actions-qemu",
    }
    if args.openwrt_version:
        metadata["openwrt_version"] = args.openwrt_version
        os.environ["OPENWRT_VERSION"] = args.openwrt_version
    if args.router:
        metadata["router"] = args.router
        os.environ["ROUTER_MODEL"] = args.router
    if args.use_case:
        metadata["use_case"] = args.use_case
        os.environ["USE_CASE"] = args.use_case
    if args.passed is not None:
        metadata["passed"] = args.passed
    if args.failed is not None:
        metadata["failed"] = args.failed
    if args.skipped is not None:
        metadata["skipped"] = args.skipped
    if args.errors is not None:
        metadata["errors"] = args.errors

    print(f"Publishing conwrt test results: {results_dir}")
    print(f"  Run ID: {args.run_id}")
    print("  Project tag: conwrt")
    print(f"  Blossom: {args.blossom_server}")
    print(f"  Relays: {args.relays}")
    print()

    _generate_summary_json(results_dir, args.passed, args.failed, args.skipped, args.errors)

    manifest = publish_results(
        results_dir=str(results_dir),
        nsec_file=str(nsec),
        run_id=args.run_id,
        blossom_server=args.blossom_server,
        relays=[r.strip() for r in args.relays.split(",")],
        metadata=metadata,
    )

    print("\nResults published!")
    print(f"  Summary event: {manifest.get('summary_event_id', '?')}")
    print("  View at: https://tests.tollgate.me/ (filter: conwrt)")


if __name__ == "__main__":
    main()
