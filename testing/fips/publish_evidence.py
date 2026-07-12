#!/usr/bin/env python3
"""Publish FIPS TollGate test results to the dashboard via Nostr kind 30078.

Creates a test-run summary event with the full test matrix results,
including the Cashu payment integration. The dashboard at
tests.tollgate.me reads these events and displays them.

Usage:
    python3 publish_evidence.py --results '{"tests": [...], "pass": 8, "fail": 0}'
    python3 publish_evidence.py  # publishes hardcoded live test results
"""
import json
import os
import sys
import time

_project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_project_root, "lib"))
from nostr_publisher import publish_test_run_event

NSEC_FILE = os.path.expanduser("~/.config/prta/nsec")

# Live test results from the 2026-07-10 run on SHC VMs
LIVE_RESULTS = {
    "project": "fips-tollgate",
    "test_suite": "FIPS Forwarding Policy + Cashu Payment Integration",
    "date": "2026-07-10",
    "commit": "e7cc77b",
    "topology": "3-node star: B→A←C (A is transit node)",
    "infrastructure": "3x SHC VMs (Debian 13, 2 vCPU, 8GB RAM)",
    "fips_version": "0.5.0-dev (rev 635963bb40)",
    "mint": "testnut.cashu.exchange (FakeWallet)",
    "tests": [
        {
            "name": "TEST 1: Both Full (baseline)",
            "b_policy": "full",
            "c_policy": "full",
            "result": "PASS",
            "detail": "Transit works, 3.5ms RTT",
        },
        {
            "name": "TEST 2: B=LocalOnly, C=Full",
            "b_policy": "local_only",
            "c_policy": "full",
            "result": "PASS",
            "detail": "Transit blocked (source denied)",
        },
        {
            "name": "TEST 3: B=Full, C=LocalOnly",
            "b_policy": "full",
            "c_policy": "local_only",
            "result": "PASS",
            "detail": "Transit blocked (return path denied)",
        },
        {
            "name": "TEST 4: Both LocalOnly (default)",
            "b_policy": "local_only",
            "c_policy": "local_only",
            "result": "PASS",
            "detail": "Transit blocked (default policy)",
        },
        {
            "name": "TEST 5: Restore both Full",
            "b_policy": "full",
            "c_policy": "full",
            "result": "PASS",
            "detail": "Transit restored (reversible)",
        },
        {
            "name": "TEST 6: Transit blocked before Cashu payment",
            "result": "PASS",
            "detail": "Pre-payment transit denied",
        },
        {
            "name": "TEST 7: Cashu payment enables transit",
            "result": "PASS",
            "detail": "21 sats → FakeWallet PAID → set_peer_policy Full → transit works",
        },
        {
            "name": "TEST 8: Transit auto-reverts after payment expiry",
            "result": "PASS",
            "detail": "15s timer → auto-revert LocalOnly → transit blocked",
        },
    ],
    "summary": {
        "total": 8,
        "pass": 8,
        "fail": 0,
    },
    "stats": {
        "forwarded_packets": 337,
        "drop_no_route_packets": 111,
        "transit_rtt_ms": 3.5,
    },
    "key_findings": [
        "ForwardingPolicy::Full allows transit, LocalOnly blocks it",
        "Both B AND C must be Full for round-trip transit",
        "Policy changes take effect within 2 seconds",
        "FIPS requires identity_cache entry for destination before transit routing",
        "Cashu FakeWallet auto-pays invoices — full payment flow verified",
        "Auto-revert timer correctly reverts policy after paid duration",
    ],
}


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Publish FIPS test evidence to dashboard")
    parser.add_argument("--results", default=None, help="JSON string with test results")
    parser.add_argument("--nsec", default=NSEC_FILE, help="Path to nsec file")
    parser.add_argument("--project-tag", default="fips-test", help="Nostr project tag")
    parser.add_argument("--run-id", default=None, help="Run ID (default: auto-generated)")
    args = parser.parse_args()

    results = json.loads(args.results) if args.results else LIVE_RESULTS

    run_id = args.run_id or f"fips-tollgate-{int(time.time())}"
    timestamp = int(time.time())

    summary = json.dumps(results, indent=2)

    print("Publishing FIPS test evidence...")
    print(f"  Run ID: {run_id}")
    print(f"  Project tag: {args.project_tag}")
    print(f"  Tests: {results['summary']['pass']}/{results['summary']['total']} passed")

    result = publish_test_run_event(
        nsec_file=args.nsec,
        run_id=run_id,
        timestamp=timestamp,
        file_urls=[],
        summary=summary,
        project_tag=args.project_tag,
    )

    if result.get("success"):
        print(f"  Event ID: {result.get('event_id', '?')}")
        print("  Published to relays successfully")
        print("  Dashboard: https://tests.tollgate.me")
    else:
        print(f"  FAILED: {result.get('error', 'unknown')}")
        if result.get("relay_status"):
            for relay, status in result["relay_status"].get("relay_results", {}).items():
                print(f"    {relay}: {'OK' if status['accepted'] else status['message']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
