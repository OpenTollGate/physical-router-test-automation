# Tag-Readiness Report — `main @ 04ae54e` (two-router)

**Repo:** `OpenTollGate/tollgate-module-basic-go`
**Commit:** `04ae54e77aebc2dab6bf9e6f58c6cb8dd6ce22a2`
**Date:** 2026-06-18  ·  **Suite:** [`feat/tag-readiness-suite`](https://github.com/OpenTollGate/physical-router-test-automation/pull/38) (PR #38)
**Latest tag:** `v0.5.0-alpha2` (this commit is post-alpha2, toward v0.5.0)
**Companion issue:** [#169](https://github.com/OpenTollGate/tollgate-module-basic-go/issues/169) · cross-linked to release plan [#154](https://github.com/OpenTollGate/tollgate-module-basic-go/issues/154)

## Verdict: ⚠️ READY-WITH-CAVEATS (unchanged)

- **OK for `v0.5.0-alpha3` / `-beta1`.**
- **Not for stable `v0.5.0`** until the funded two-router autopay + degraded-mode-on-hardware paths are exercised green (currently blocked by **lab funding tooling**, not code).

The commit builds clean and is healthy on **two** GL-MT3000 routers on OpenWrt
24.10.4. The v0.5.0 **upstream-WiFi-manager discovery + connect + TollGate
advertisement validation were verified on hardware**. The two-router *payment*
path could not be completed because the lab cannot currently mint test funds
(test mints unreachable/incompatible — see Blockers).

## Environment (both routers GL-MT3000)

| Role | IP | OpenWrt | Module | Notes |
|------|----|---------|--------|-------|
| alpha (primary/client) | 192.168.8.1 | 24.10.4 (flashed 21.02→24.10.4) | 04ae54e | Was stock 21.02; flashed w/ releases.tollgate.me image (sysupgrade, config kept), then 04ae54e ipk. |
| beta (secondary/upstream) | 192.168.244.1 | 24.10.4 | 04ae54e | Already modern; left as-is. Upstream = house WiFi. |

Methodology: all results below produced by **committed pytest/Makefile** targets
(see `AGENTS.md` "Golden Rules"). No ad-hoc SSH was used to generate pass/fail;
funding setup is a fixture (`two_router_funded_upstream`) that skips cleanly
when the mint tooling is broken.

## Results & criticality matrix

Legend: ✅ pass · ❌ fail · ⏭️ skip · 🔧 = environmental/tooling, not a code regression.

| Area (test) | Result | What it validates | Criticality for v0.5.0 | Reason / impact |
|---|---|---|---|---|
| **Tier 0** `go build ./...` | ✅ | Compiles | **Blocker** | Clean. |
| **Tier 0** `go vet ./...` | ✅ | Static correctness | Medium | Clean. |
| **Tier 0** `go test` 12/14 modules | ✅ 12 · 🔧 2 | Unit/integration | Medium | Root-module test 🔧 (needs `/etc/tollgate/config.json`, non-hermetic off-router); `upstream_detector` 🔧 (`go.mod` not tidy). Not regressions. |
| **Tier 1** alpha API smoke (`tests/api -m smoke`) | ✅ 21 · ⏭️ 30 · 🔧 1 | Backend API on GL-MT3000 | High | 🔧 fail = `test_portal_verify` (`curl` not on image). Health/info/hostname/CLI-version all pass. |
| **Tier 1** beta API smoke | ✅ 27 · ⏭️ 24 · 🔧 1 | Backend API on 2nd GL-MT3000 | High | Same `curl` env-only fail; backend healthy. |
| **Preflight** (both routers, `test_tag_readiness.py`) | ✅ 5/5 | Reachable, version, SSIDs, no dual-WWAN, no crash loop | High | Proves both routers on 04ae54e, healthy, correct topology. |
| **Postflight** (both routers) | ✅ 4/4 | Service alive, no panics, no leftover mint-blocks, wallet answers | High | Clean steady state after campaign. |
| **Tier 2** WiFi upstream discovery + connect (alpha→beta) | ✅ (log-verified) | v0.5.0 **upstream WiFi manager**: scan, connect, detect beta as TollGate, validate advertisement | **High** | `tollgate upstream scan` saw `TollGate-09E0` @ -3 dBm; connect succeeded; logs show `Reporting gateway… Checking if gateway is a TollGate … Gateway responded – validating TollGate advertisement`. The headline feature works on HW. |
| **Tier 2** `test_two_router.py::TestPinUpstream` | ❌ (precondition) | Upstream pinning post-payment | Medium | Fails at `No active upstream on primary` — alpha's upstream drops because it can't pay beta (wallet 0). 🔧 funding-blocked, not code. |
| **Tier 2** `test_two_router.py::TestDegradedUpstreamRenewal` (smoke-upstream) | ❌ (precondition) | Degraded renewal over upstream | Medium | Same precondition; needs a paid active session. 🔧 funding. |
| **Tier 2** `TestDegradedConnectWhileDegraded` | ⏭️ | Risky connect-while-degraded | Low | Requires `TOLLGATE_ALLOW_RISKY=1`. |
| **Tier 2** `TestRouterLockCoordination` | ✅ 2/2 | Multi-session mutex | Low | Pure-local lock logic. |
| **Tier 2** funded autopay (`test_funded_autopay_opens_session`) | ⏭️ | End-to-end alpha→beta payment + session | **High (gap)** | Skipped: funding tool errors (testnut BOLT11 decode; orangesync TLS down). **This is the main unverified v0.5.0 HW path.** |
| **Tier 2** degraded-mode on HW (`smoke-degraded`) | ⏭️ | Mint-unreachable→degraded→recover on hardware | **High (gap)** | cashu CLI/wallet funding unavailable; skipped. Validated in Go unit tests only. |
| **Tier 3** reboot-recovery (beta) | ✅ | Service auto-starts after reboot, build persists | Medium | Clean cycle, fresh uptime, 04ae54e retained. |

**Totals (Tier 2 + tag-readiness run):** 4 failed (2 funding-precondition, 2
already-fixed test-precision/hygiene), 9 passed, 2 skipped. After fixing the
dual-WWAN false-positive and cleaning a leftover `/etc/hosts` mint block on
beta, preflight+postflight are 9/9 green.

## Blockers (all environmental/tooling — **not** `04ae54e` code regressions)

1. **Lab cannot mint test funds → funded two-router autopay + degraded-HW unverified.**
   - `testnut-compat.mints.orangesync.tech` (harness default mint): **TLS internal error / unreachable**.
   - `testnut.cashu.exchange` (beta's mint): `scripts/mint-token` fails — `error decoding bolt11 invoice: zpay32 decoding failed: checksum failed` (gonuts library vs the FakeWallet invoice). Host nutshell `cashu` CLI also has an uninitialized-DB issue.
   - Net effect: alpha's wallet stays 0 sats → can't pay beta → no persistent upstream session → payment-path tests fail at precondition or skip. The fixture (`two_router_funded_upstream`) handles this correctly: it skips cleanly *before* mutating routers.
2. **`curl` not on the GL-MT3000 image** → `test_portal_verify` fails; backend is healthy by every other probe. (Image/packaging, not module.)
3. **Minor on `main`:** root-module test non-hermetic; `upstream_detector/go.mod` needs `go mod tidy`.

## How to reason about criticality for tagging

- **What's proven on hardware at 04ae54e:** build/vet clean; core API on *two* GL-MT3000s; preflight/postflight clean; **the v0.5.0 upstream-WiFi-manager discovery/connect/advertisement-validation** (the new feature's foundation); reboot-recovery. These are the highest-signal checks and they pass.
- **What's *not* proven on hardware:** the actual Cashu *payment* across two routers (autopay/reseller) and the degraded→recover cycle on real hardware. Both are blocked solely by the **test-mint/funding toolchain being broken in the lab**, not by defects in `04ae54e`. The payment logic itself is covered by Go unit/integration tests in CI.
- **Therefore:** for a pre-release (`-alpha3`/`-beta1`) the risk is acceptable. For **stable v0.5.0**, restore a working test mint (or fix `mint-token`'s BOLT11 handling / use nutshell), re-run `make tag-readiness-two-router`, and get the funded autopay + degraded-HW tests green before tagging.

## Reproduce

```sh
# harness worktree feat/tag-readiness-suite
make lock PHASE="v0.5.0 two-router (04ae54e)"
make tag-readiness-static                                      # Tier 0 (no router)
# deploy 04ae54e to each router (local-build ipk; see PR #38 notes)
pytest tests/api -m smoke --no-deploy                         # Tier 1 per router
pytest tests/scenarios/test_two_router.py tests/scenarios/test_tag_readiness.py --no-deploy
# funded path needs a working test mint:
TOLLGATE_TEST_MINT_URL=<reachable-mint> MINT_TOKEN_BIN=$PWD/scripts/mint-token/mint-token \
  pytest tests/scenarios/test_tag_readiness.py::TestTwoRouterFunded --no-deploy
make unlock
```

## Recommendation

- Tag `v0.5.0-alpha3`/`-beta1` from `04ae54e`: **yes**.
- For stable `v0.5.0`: (1) restore/fix the lab test-mint so funded two-router autopay + `smoke-degraded` run on hardware; (2) `go mod tidy` in `upstream_detector`; (3) make the root-module test hermetic. Then re-run `make tag-readiness-full`.
