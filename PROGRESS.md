# QR CI prerequisite progress — 2026-11-10

- Verified PR #340 exact head and DRAFT state; original checkout clean; isolated
  branch `fix/qr-ci-prereq-20261110` created from cb7d388. No concurrent process
  owning this QR branch observed; remote will be checked again before push.
- Read failed fork logs; fresh pristine-base/head Python full-suite comparison
  confirms identical 11 failed/error IDs. Separate repair proposals in REPORT.
- Supported runner selected from locked Playwright 1.59.1's platform registry,
  not an arbitrary OS downgrade. Only lint runner changed; safety gates intact.
- Tested milestone: 71 JavaScript tests collected; six helper imports passed;
  YAML semantic parity except lint runner and diff whitespace check passed.
- Next: fast-forward push existing QR PR branch; dispatch existing safe fork
  CI and Python CI; record exact run IDs/SHA outside the commit (avoid moving
  the tested head merely to record its own run). Keep DRAFT and report Python
  baseline failures as blockers, never bypass them.

---

# PROGRESS — t_a4aa351d (Consolidate bench locking into ONE machine-global lease)

Worktree: /home/c03rad0r/worktrees/prta-revive  branch pr/prta-revive-hw-dispatch
Head: 6ceb9fe (PUSHED to fork, ls-remote verified)

## Attempt 1 (run 21, timed out) — recovered
- 94f3315 lease + 435d72e dispatch lane already pushed.
- ~730 lines uncommitted work recovered, reviewed, verified, committed as 28cbbe3.

## Attempt 2 (run 26) — done
- 28cbbe3 finish consolidation: esp32 board leases, mint-health, bench.py,
  poll-queue CHECKOUT bug, legacy sweep + prune-legacy, idle-gate fail-closed
  fix (RED-first test), evidence logs. PUSHED.
- 6ceb9fe regression fix: pytest could take the PRODUCTION lease (3 scenario
  modules bypass conftest's gate) + force_release semantics test pinned.
  PUSHED.
- Verified: 82 lease unit tests, scenario lock tests, make check-workflows 24/24,
  make lock-status end to end, production lease still free after test runs.
- Evidence: evidence/prta-lease/{ACCEPTANCE.md,concurrent-run.log,
  stale-reclaim.log,board-concurrent-run.log}
- REPORT.md written.

## Remaining (for the sibling card t_30255dee, not this one)
1. Trigger the dispatch workflow and capture a run URL (needs runner/token; bench
   not reachable from this host — 192.168.1.1 not pingable).
2. Nothing else outstanding for this card's acceptance criteria.
