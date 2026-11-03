#!/usr/bin/env python3
"""Diagnostic-only place-holding timing against a LIVE coordinator.

Not Gate E or production scheduler qualification: no assertion-bearing workload,
fleet authority admission, TTL/fence supervision, or verified cleanup.

Not a test, not a mock: a measurement. It acquires real places on the given
coordinator, holds each for a fixed quantum, releases, and reports wall clock for
serial then parallel.

The number it produces is an UPPER BOUND on a real test suite's speedup, because
the lanes here are identical in duration by construction. Real suites have
lopsided lanes, where the critical path -- not the average -- sets the wall clock.
Reported alongside the measurement so the bound cannot be quoted as a result.

Usage:
    bench-throughput.py --coordinator x280.fips:20408 \
        --place gl-mt3000 --place comfast --quantum 3
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor


def hold(addr: str, place: str, quantum: float) -> tuple[str, float]:
    started = time.perf_counter()
    got = subprocess.run(
        ["labgrid-client", "-x", addr, "-p", place, "acquire"],
        capture_output=True,
        text=True,
    )
    if got.returncode != 0:
        print(f"  !! acquire {place} failed: {got.stderr.strip()[:160]}", file=sys.stderr)
        raise SystemExit(2)
    try:
        time.sleep(quantum)
    finally:
        subprocess.run(
            ["labgrid-client", "-x", addr, "-p", place, "release"],
            capture_output=True,
            text=True,
        )
    return place, time.perf_counter() - started


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coordinator", required=True)
    ap.add_argument("--place", action="append", required=True)
    ap.add_argument("--quantum", type=float, default=3.0)
    args = ap.parse_args()

    if len(args.place) < 2:
        print("need at least 2 places to compare serial against parallel", file=sys.stderr)
        return 2

    print("DIAGNOSTIC ONLY: not Gate E or production scheduler qualification")
    print(f"coordinator: {args.coordinator}")
    print(f"lanes:       {', '.join(args.place)}")
    print(f"quantum:     {args.quantum}s per lane\n")

    print("serial:")
    t0 = time.perf_counter()
    for place in args.place:
        _, took = hold(args.coordinator, place, args.quantum)
        print(f"  {place:16} {took:6.2f}s")
    serial = time.perf_counter() - t0
    print(f"  {'TOTAL':16} {serial:6.2f}s\n")

    print("parallel:")
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(args.place)) as pool:
        results = list(pool.map(lambda p: hold(args.coordinator, p, args.quantum), args.place))
    parallel = time.perf_counter() - t0
    for place, took in results:
        print(f"  {place:16} {took:6.2f}s")
    print(f"  {'TOTAL':16} {parallel:6.2f}s\n")

    speedup = serial / parallel if parallel else 0.0
    print(f"serial   {serial:6.2f}s")
    print(f"parallel {parallel:6.2f}s")
    print(f"speedup  {speedup:6.2f}x  over {len(args.place)} lanes")
    print(f"ideal    {len(args.place):6.2f}x  (it is an upper bound, not a target)")
    return 0 if speedup > 1.05 else 1


if __name__ == "__main__":
    raise SystemExit(main())
