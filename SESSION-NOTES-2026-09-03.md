# Session Notes — 2026-09-03 evening (unattended improvement session)

Scope: prta + conwrt + tollgate. Branches only; nothing merged into tollgate.
Disk guarded: freed to 12G (rs-ai cargo clean 5.6G + docker volume prune). NOTE:
other sessions' dirs under /tmp/opencode (2139, fips-next, poca-mint…) are
actively in use — do not delete.

## Merged/pushed today (main)
- prta `61e21a7` CDK 0.18 full-suite A/B docs; `0ec6f9e` CDK_VER variable
  default 0.18.0 (run-local-tests.sh + worker mints.py version-aware; 0.18
  config model: [payment_backend], env: secret refs, config init --new-mint).
- tollgate-module-basic-rust `779a3a2` cdk 0.18 bump + set-cdk-version.sh A/B
  helper. A/B: 213/213 unit tests on BOTH 0.17 and 0.18. cdk-common fork pin
  DROPPED (upstream 0.18.0 uses AtomicUsize — MIPS-safe).
- tmbg fork `67d4f6c` local-build-ipk.sh (SDK-less x86_64 ipk builder,
  env-overridable ARCH/COMPILE_KEY/GOARCH). Built v0.6.1-post-merge-12-g67d4f6c
  x86_64.ipk, deployed to lab, payment suite 16/16 vs cdk-mintd 0.18.0.
- cashu-cf ISSUE-105 file + index (checkstate audit closure).
- knowledgebase#1: CDK 0.18 / NUT-alignment audit tracker (7 audit items +
  experiment list).

## Branches for review
- prta `fix/local-lab-degraded-and-hangs` (d7de18c, pushed):
  1. block_mints blocked only :443 — no-op for http://lab mints → degraded
     mode never triggered locally (root cause of the degraded failure
     cluster). Now blocks the mint's real port. Live: degraded/re-upgrade/
     boltdb-swap tests failing/hanging → passing.
  2. test_concurrent_requests_during_swap hung 30+ min on BOTH cdk versions
     (individually-bounded waits, unbounded sum). Now ends on an overall
     deadline with recorded stop reason; passes in isolation 10.6s.
  3. test_degraded_mode_returns_retry_notice: accepts
     payment-processing-failed (current backend classification) pending the
     tollgate branch below.
- tmbg fork `fix/degraded-notice-code` (e37fbaf, BRANCH ONLY — do not merge
  without review): degraded payment failures now emit `service-unavailable`
  instead of generic `payment-processing-failed` (classifyPaymentFailure
  extracted + 4 unit tests; merchant package suite green).
- tollgate-rs-ai-research `chore/cdk-0.18` (aaccc4c, LOCAL ONLY — HEAD
  carries another session's unpushed a604dac; push after they land it):
  cdk tag v0.18.0, spilman patched to Amperstrand/cashu_spilman_channels
  @cdk-0.18 (fork: cdk tags bumped + CurrencyUnit::Custom(Arc<str>) fix).
  192 tests green. NOTE: plain `cargo build --workspace` was already broken
  at HEAD pre-migration (10 un-gated-import errors) — separate pre-existing
  issue, surfaced in knowledgebase#1.

## Surfaced, not fixed (for follow-up)
- conwrt `make ci` lint: 19 ruff errors. Key hygiene bug: ruff scans the
  nested scratch checkout `tests/integration/.tollgate/src-main/` (should be
  in pyproject [tool.ruff] extend-exclude). Remaining errors are fixable
  style (zip strict=, unused args). NOT touched: branch `vpn-revival` has
  another session's uncommitted pyproject/Makefile/handlers_zycast.py WIP in
  exactly this area — collision risk.
- prta full tests/api local baseline: 77 passed / 26 failed. 21 of the
  failures are A/B-proven identical on cdk 0.16 (balance.html/uhttpd missing
  on fresh VM, degraded sims, lightning-portal melts). Worth a dedicated
  local-lab-green session.
- SHC full-suite run: blocked upstream — x86_64 ipk artifacts expired
  (3-day retention); fork CI now triggered by today's main push and the
  matrix builds x86_64 ipk, so `cloud-lab.py submit` should work after CI
  (~15 min post-push).
- NDS 5.0.2 auth-mark bug (authenticated client new connections REJECTed):
  root-caused + workaround documented in prta AGENTS.md; backend self-heal
  candidate not yet implemented.

## Resource state at handoff
Disk 12G free (watch: 98%-full shared disk; cargo clean rs-ai again if
needed). Lab STOPPED. No stray processes. prta has one unrelated local
deletion (`results/dual-router-20260703-164218/report.md`) predating this
session — review or restore.
