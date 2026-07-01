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
import os
import sys
from pathlib import Path

SUITE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE_ROOT))

os.environ.setdefault("PROJECT_TAG", "conwrt")

from lib.result_publisher import publish_results


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
    print(f"  Project tag: conwrt")
    print(f"  Blossom: {args.blossom_server}")
    print(f"  Relays: {args.relays}")
    print()

    manifest = publish_results(
        results_dir=str(results_dir),
        nsec_file=str(nsec),
        run_id=args.run_id,
        blossom_server=args.blossom_server,
        relays=[r.strip() for r in args.relays.split(",")],
        metadata=metadata,
    )

    print(f"\nResults published!")
    print(f"  Summary event: {manifest.get('summary_event_id', '?')}")
    print(f"  View at: https://tests.tollgate.me/ (filter: conwrt)")


if __name__ == "__main__":
    main()
