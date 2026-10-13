# Live-commission read-only violation — 2026-10-01

> **Reconstruction note (2026-10-13):** the original postmortem written
> the day of the incident was never committed; this file was
> reconstructed from the incident section of `AGENTS.md` (the surviving
> authoritative summary) and annotated with the fix-queue status as of
> 2026-10-13. Nothing here is new analysis — only reorganization plus
> status updates backed by merged code and tests.

## What happened

A "read-only" commissioned run against the MT3000 (tollgate-326D)
mutated live router state. `deploy_session` (autouse,
`tests/conftest.py:539-556` at the time) ran `enable_debug_portal()` +
`ensure_test_mint()` + `replace_mints()` on **every run that lacks
`--no-deploy`** — even with zero deploy flags. The run **replaced a live
router's 8-mint `accepted_mints` with `testnut.cashu.exchange` and
restarted the backend twice**.

`--no-deploy` is named after deployment but was the ONLY suppressor of
state prep. Live commissions had to know to pass it; nothing failed
closed.

## Contributing defect

`replace_mints()` had a destructive default: `mint_urls=None` →
`[TEST_MINT_URL]` — a bare call silently rewrote the operator's mint
list (`lib/router.py:834` at the time).

## Detection and recovery were luck

- Detection: pytest live-log happened to print "Replaced accepted
  mints…".
- Recovery: `logread` boot lines enumerated the pre-run mints, the m5
  lane's config backup existed, and mutating methods were
  read-modify-write so only `accepted_mints` drifted.

## The skip forest (the second finding)

The same commission ran 8 passed / 18 skipped — and **15 of the skips
assert the pre-PRTA-#103 net4sats portal layout** and can never pass on
current packaging. The `:2051` SPA portal had **zero passing pytest
coverage** while the dashboard read "0 failed".

## Unpinned contracts observed (never asserted anywhere)

- `:2051` bare `/` answers **403** (intended? never decided)
- `session-state?mac=X` → `{"status":1,"mac":"","state":"none"}`
- `/usage` sentinel `-1/-1`

## Running-state vs on-disk config drift

The backend pid probed a mint (`8333.space`) absent from on-disk config
— either a config edit without restart or `wallet.db` registration
outliving config removal. Unresolved; rig experiment queued.

## Fix queue — status as of 2026-10-13

| Item | Status | Where |
|---|---|---|
| **F1** `--read-only` fail-closed flag | **LANDED** — PR #231: 30 `@_mutating` Router methods raise `RouterReadOnlyError` before any command; `deploy_session` refuses deploy flags, skips state prep; `config`/`destructive`-marked tests skip with a visible reason | `lib/router.py`, `tests/conftest.py`, `tests/unit/test_read_only_guard.py` |
| **F3** `replace_mints` guards | **LANDED** — explicit list required, >1→1 shrink needs `force` | `lib/router.py`, `tests/unit/test_replace_mints_guard.py` |
| **F4** snapshot/diff/restore (`scripts/live-snapshot.py`) + commissioned-run template v2 | OPEN | — |
| **F7** portal rewrite behind `_portal_layout()` detection | **LANDED** — PR #231: `portal_layout()` + `TestSpaPortal2051`; soak-verified 4/4 on a live VM (see `docs/local-lab-soak-2026-10-13.md`) | `lib/helpers.py`, `tests/api/test_portal_verify.py` |
| **F8** pin the unpinned contracts in `config/behavior-contract.json` | OPEN | — |
| **F9** labgrid coordinator reconciliation (AGENTS said `192.168.13.208`, rig doc `192.168.13.221`; nr7101 place broken; root ser2net squats the FT232R) | OPEN | — |

## Standing rule until F4 lands

Commissioned/live runs must pass BOTH `--no-deploy` and (since F1)
`--read-only`; `--read-only` is the fail-closed one — a mutating call
raises instead of relying on the operator remembering a flag.
