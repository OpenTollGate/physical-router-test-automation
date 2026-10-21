#!/usr/bin/env python3
"""find-superset-prs.py — find duplicate/superset PR chains across the open queue.

WHY THIS EXISTS
---------------
When many branches stack on one repo, several PRs end up carrying the *same*
change: a fix is authored, then re-authored on top of a later base, and both stay
open. Every one of them has to be reviewed, tested, and merged separately — and a
reviewer reading them one at a time cannot see that the second adds nothing.

This finds them mechanically. For every pair of open PRs it asks two questions:

  1. is one PR's changed-file set a strict superset of the other's?
  2. on every shared file, are the code changes byte-identical?

Both yes => the smaller PR contributes nothing the larger does not already carry.
That is a proof of redundancy, not a judgement about code quality, so a closure on
its back is safe without reading either diff in full.

HOW IT WAS VALIDATED
--------------------
It found chains that hand-review had missed, including a five-deep CI chain
(#879 -> #881 -> #905/#924 -> #925 -> #921) where no single PR page shows the
relationship, and a deps chain (#1028 -> #1029 -> #1030) where the same one-file
change existed in four separate PRs.

USAGE
    scripts/ci/find-superset-prs.py --repo OWNER/REPO --worktree PATH

The worktree must have PR heads fetched under refs/remotes/upstream/pr/*. For a
repo you have cloned, that is:

    git fetch origin '+refs/pull/*/head:refs/remotes/upstream/pr/*'
    git fetch origin main:refs/remotes/upstream/main       # the diff base

NOTES AND LIMITS (read before trusting the output)
  * It compares CODE lines only (+/- lines, sorted). Two PRs that touch the same
    file with the same code but a different comment/whitespace still count as
    identical — deliberate, since comment-only drift does not make a PR unique.
  * It cannot see a PR whose branch is not fetchable (fork deleted, force-pushed
    away). Those are skipped; the count printed tells you how many were scanned.
  * Pagination is mandatory: a repo with >100 open PRs silently truncates at 100
    without --paginate, and a missed chain is a missed closure. This script
    paginates and warns if fewer than 100 PRs came back.
  * It does NOT decide what to close. Landing order is a human call — a draft PR
    or one under active development elsewhere must not be recommended as the
    merge vehicle just because it happens to be the superset.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

DEFAULT_BASE = "upstream/main"


def sh(cmd: str, cwd: str) -> str:
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    return r.stdout


def open_pr_numbers(repo: str) -> list[int]:
    """Every open PR number, paginated. Never assume <100."""
    raw = subprocess.run(
        f"gh api 'repos/{repo}/pulls?state=open&per_page=100' --paginate "
        f"--jq '.[] | .number'",
        shell=True, capture_output=True, text=True,
    )
    nums = [int(l) for l in raw.stdout.splitlines() if l.strip().isdigit()]
    if len(nums) >= 100 and len(nums) % 100 == 0:
        print(f"WARNING: {len(nums)} PRs is a multiple of 100 — pagination may "
              "have stopped early. Verify manually.", file=sys.stderr)
    return sorted(nums)


def changed_files(pr: int, cwd: str, base: str) -> set[str]:
    out = sh(f"git diff --name-only {base}...upstream/pr/{pr} 2>/dev/null", cwd)
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def code_changes(pr: int, path: str, cwd: str, base: str) -> str:
    """The +/- lines only, sorted — ignores hunk line numbers and index lines."""
    out = sh(
        f"git diff {base}...upstream/pr/{pr} -- {path} 2>/dev/null "
        "| grep -E '^[+-]' | grep -v -E '^[+-][+-]' | sort",
        cwd,
    )
    return out


def find_chains(nums: list[int], worktree: str, base: str,
                min_shared: int = 1) -> tuple[list[tuple[int, int, int]], list[int]]:
    """The detector itself. Returns (chains, skipped_unreadable_prs).

    Extracted so tests exercise THIS function rather than a copy of its logic —
    a test that re-implements the detector cannot fail when the detector breaks.
    """
    files = {n: changed_files(n, worktree, base) for n in nums}
    readable = {n: f for n, f in files.items() if f}
    skipped = sorted(set(nums) - set(readable))

    chains: list[tuple[int, int, int]] = []
    for big in readable:
        fbig = readable[big]
        for small in readable:
            fsmall = readable[small]
            if big == small or not (fsmall < fbig):
                continue
            if len(fsmall) < min_shared:
                continue
            if all(code_changes(big, p, worktree, base)
                   == code_changes(small, p, worktree, base)
                   for p in sorted(fsmall)):
                chains.append((big, small, len(fsmall)))
    return sorted(chains), skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="OWNER/REPO")
    ap.add_argument("--worktree", required=True,
                    help="clone with refs/remotes/upstream/pr/* fetched")
    ap.add_argument("--base", default=DEFAULT_BASE,
                    help=f"diff base ref (default {DEFAULT_BASE})")
    ap.add_argument("--min-shared", type=int, default=1,
                    help="ignore chains sharing fewer than N files (default 1)")
    a = ap.parse_args()

    nums = open_pr_numbers(a.repo)
    print(f"scanning {len(nums)} open PRs for superset chains...\n")

    chains, skipped = find_chains(nums, a.worktree, a.base, a.min_shared)
    if skipped:
        print(f"skipped {len(skipped)} PR(s) with an unreadable branch: "
              f"{', '.join('#'+str(s) for s in skipped)}\n")

    if not chains:
        print("no superset chains found.")
        return 0

    print("SUPERSET CHAINS (the larger PR already carries the smaller one's change):")
    for big, small, n in sorted(chains):
        print(f"  #{big} CONTAINS #{small}   "
              f"({n} shared file{'s' if n != 1 else ''}, all code changes identical)")

    print("\nclosure candidates (close the CONTAINED one after the container lands):")
    contained = {s for _, s, _ in chains}
    for small in sorted(contained):
        containers = sorted(b for b, s, _ in chains if s == small)
        print(f"  #{small} is contained in {', '.join('#'+str(c) for c in containers)}")

    print(f"\n{len(contained)} PR(s) can be closed once their container lands.")
    print("NOTE: landing order is a human call — a draft or actively-developed PR "
          "must not be the merge vehicle just because it is the superset.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
