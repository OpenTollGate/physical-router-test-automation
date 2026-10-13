# PRTA as the Authoritative Test Suite — Design Direction

## The Problem: Cross-Implementation Drift

We have multiple TollGate implementations (net4sats, tollgate-module-basic-go,
tollgate-module-basic-rust, ESP32/tollgate-core, NR7101 builds) that are
drifting apart in behavior:

- **Portal ports differ**: :80 vs :2050 vs :2051 per build
- **Portal copy differs**: "Generate Invoice" vs "Purchase Internet Access"
- **Default tab differs**: Lightning-first vs Cashu-first per build
- **Pricing UI differs**: .size-btn preset tiers vs amount stepper
- **Token format acceptance differs**: V3-only vs V3+V4, cashuA vs cashuB
- **Preauth rules differ**: some allow mint pre-payment, some don't
- **NDS chain behavior differs**: mark rules, auth-mark bug presence

Nobody notices until a manual test fails on one rig but passes on another,
and nobody can say WHICH behavior is correct.

## The Solution: PRTA as the Single Point of Authority

PRTA should be the **behavior contract** — the place you go to answer
"what is SUPPOSED to happen when a user joins a TollGate WiFi and pays
a token?" Every implementation must pass the same user-story tests.

## Design Principles

### 1. User Stories, Not Implementation Details

Tests should read like a user's experience, not a developer's debug session:

```gherkin
Feature: Phone payment through captive portal
  Scenario: User pays with a Cashu token
    Given the phone joins the open TollGate SSID
    And the captive portal appears
    When the user submits a valid Cashu token
    Then internet access is granted within 30 seconds
    And the phone can load a real webpage
    And an external IP is visible
```

NOT:
```
Test: POST to :2121 with Content-Type text/plain, expect kind:1022
```
(That's an API test, not a user story.)

### 2. Behavior-Based Assertions, Not Selector-Based

Assert WHAT the user sees, not WHICH CSS class renders it:

```javascript
// ✅ Behavior: "the portal shows Lightning payment copy"
await expect(page.getByText(/pay with.*lightning/i)).toBeVisible();

// ❌ Implementation: "the .tollgate-captive-portal-method-lightning class has an h2"
const payLine = document.querySelector('.tollgate-captive-portal-method-lightning .tollgate-captive-portal-method-header h2');
```

This makes tests survive UI refactors across implementations.

### 3. Film Well (Evidence-First)

Every test should produce evidence that a stakeholder can review:

- Screen recording (video)
- Step-by-step screenshots with claim tags
- Vision-AI validation of each screenshot against its claim
- External IP capture for internet-verification tests

This is already built (lib/clients/evidence.py). Extend it to browser tests.

### 4. Contract JSON: The Expected-Behavior Registry

A versioned file that declares what "correct" looks like per capability:

```json
{
  "portal": {
    "entry_url": "/splash.html",
    "default_tab": "implementation-defined",
    "tabs": ["lightning", "cashu"],
    "ports": {"portal": 2051, "nds_gateway": 2050, "backend": 2121}
  },
  "payment": {
    "token_formats": ["cashuA (V3)", "cashuB (V4)"],
    "content_type": "text/plain",
    "response_success": "kind:1022",
    "auth_timeout_seconds": 30
  },
  "internet": {
    "validation": "Android VALIDATED capability",
    "probe_host": "8.8.8.8",
    "external_ip_service": "ifconfig.me"
  }
}
```

Tests read this and assert against it. When an implementation changes
behavior, the contract JSON changes in the same PR, and the drift is
visible in the diff.

### 5. Cross-Implementation Test Matrix

Run the same user stories against every implementation:

| Test | Go | Rust | NR7101 | ESP32 |
|------|----|------|--------|-------|
| Phone joins SSID | ✓ | ✓ | ✓ | ✓ |
| Portal appears | ✓ | ✓ | ✓ | ✓ |
| Token payment accepted | ✓ | ✓ | ✓ | partial |
| Internet granted | ✓ | ✓ | ✓ | ✗ (no upstream) |
| Tab copy correctness | ✓ | ? | ✓ | n/a |

The matrix IS the drift detector. A cell that flips from ✓ to ✗ is a
regression; a cell that was always ✗ is a known gap.

## Concrete Next Steps

### Phase 1: Consolidate existing tests into user stories
- Refactor phone tests (pytest) into named user-story scenarios
- Refactor browser tests (Playwright) to use behavior selectors
- Add contract JSON files per capability

### Phase 2: Add the missing user stories
- "User pays with a V4/CBOR token" (cashuB)
- "User's session expires and they must re-pay"
- "User sees degraded mode when mints are unreachable"
- "User switches between Lightning and Cashu tabs without copy bleed"
- "Router reboots and the user's session persists" (or doesn't)

### Phase 3: Wire into CI
- Every PR to any TollGate repo triggers PRTA tests
- Test matrix published as an artifact
- Drift = red cell in the matrix = blocked PR

### Phase 4: ESP32 and phone coverage expansion
- ESP32: portal copy, token acceptance, NDS gating
- Phone: more Android versions, more browsers, PWA install flow
- Multi-device: two phones paying simultaneously

## Anti-Patterns to Avoid

1. **Don't test implementation internals** (chain names, iptables rules)
   — those change per build and aren't user-visible
2. **Don't hardcode build-specific selectors** — use behavior/text selectors
3. **Don't skip tests on "known-different" builds** — surface the difference
   in the matrix instead
4. **Don't assert on logs** — assert on user-visible behavior
5. **Don't make tests that only developers can interpret** — a product
   manager should be able to read the test name and know what broke

## Existing Assets to Leverage

- `lib/clients/evidence.py` — video + screenshots + vision validation
- `lib/clients/u2phone.py` — Android phone automation
- `lib/clients/cdp_wallet.py` — Chrome DevTools Protocol wallet driving
- `tests/browser/portal-tab-copy.spec.mjs` — first behavior-based browser test
- `tests/phone/test_rig_phone_payment.py` — phone E2E (needs user-story naming)
- `docs/release-cache-purge.md` — cross-implementation design doc precedent

## Runner consolidation (2026-10 decision)

> The broader cross-repository rules — one owner per responsibility, the
> four-level verification hierarchy, and the AI-agent operating principles
> this decision feeds into — live in
> [engineering-policy.md](engineering-policy.md). This section remains the
> decision record for runner consolidation specifically.

**Decision.** PRTA is the single source of truth for router-test runners.
No runner in either repository may carry its own deploy/install logic or a
soft-fail verdict; deployment goes through `lib/deploy.py`, flashing through
`scripts/build-firmware.py` / `flash-routers.mjs`, and every failure that a
lane reports must affect its exit code. The
`tollgate-module-basic-go` hardware fleet (`tests/test_*.py` +
`conftest.py` + `flash_routers.py`) is **retired pending migration**: marked
deprecated in that repo's `tests/README.md`, deleted only when each mapping
row below is proven green on hardware once (the deletion gate). The
`scripts/release-test.sh` lane is frozen as the opkg-era release lane and
folds into the profile runners per the milestones below.

**What landed in M1** (gate hardening + dedup, 2026-10):

- `scripts/release-test.sh` — labgrid-lock failure refuses instead of
  proceeding without the mutex; portal tab-copy failures set the exit code;
  the inline `scp -O` + `opkg install` phase replaced by
  `lib.deploy.deploy` (era-aware apk/opkg, service restart, `:2121` health
  check).
- `tests/destructive/reboot-recovery.spec.mjs` — service-not-running after
  reboot is now a test failure, not a `console.log` WARN.

**Migration audit: main-repo fleet → PRTA equivalent.** Re-verified against
both checkouts on 2026-10-13. The striking finding: most fleet "tests"
assert nothing — they are wrappers around fixtures or interactive scripts —
so the migration risk is operational plumbing, not lost assertions.

| Main-repo file | What it actually does | PRTA equivalent | Deletion gate |
|---|---|---|---|
| `tests/test_copy_images.py` | no-op wrapper ("passes as long as the fixture completes") | `Router.scp_to`, `scripts/build-firmware.py` staging | green run using `scp_to` path |
| `tests/test_install_images.py` | flash via fixture; waits with `ping` (the ICMP anti-pattern) | `build-firmware.py --flash`, `flash-routers.mjs`, `uboot-recover.py`; post-flash state asserted by `tests/scenarios/test_install_paths.py` | one documented flash cycle |
| `tests/test_install_packages.py` | no-op wrapper, no assertions | `lib/deploy.deploy` + `tests/scenarios/test_feed_package.py` (era-aware, asserts version/identity/health) | `test_feed_package.py` green on both eras |
| `tests/test_network_configuration.py` | bespoke sshpass wait loop for upstream connectivity | `tests/scenarios/test_upstream_wifi.py`, `upstream-wifi/` targets, `Router` wait helpers | `test_upstream_wifi.py` green on hardware |
| `tests/test_ecash_payment.py` | interactive `input()`-prompted manual e2e | `tests/api/test_e2e_portal_payment.py`, `test_local_payment.py`, `tests/stories/`, `tests/protocol/payment-lifecycle.spec.mjs` | api payment suite green on a release |
| `tests/test_ecash_functionality.py` | cdk-cli wallet ops against public testnut | `tests/api/test_cli_wallet.py`, `test_cashu_compat_matrix.py`, `lib/cashu.py` (HttpMinter) | compat-matrix green |
| `tests/test_data_measurement.py` | interactive `input()` + curl download metering | `tests/phone/test_data_metering.py`, `test_time_metering.py`, `tests/protocol/data-allotment.spec.mjs` | data-allotment spec green once |
| `tests/test_teardown.py` | `assert True` placeholder | `test-pr.sh --reset` (`firstboot_reset`), `Router.reset_state` | none needed (no assertions) |

Known non-migrated behavior (recorded, not lost): the fleet's conftest
auto-discovery sweep over nearby TollGate SSIDs. It selected routers by
scanning, PRTA selects by inventory (`config/routers.json`). If
discovery-driven runs are ever wanted again, that is new PRTA
functionality, not a reason to keep the fleet.

**Milestones.**

- **M1 (landed).** Gate hardening, deploy dedup, fleet marked deprecated,
  this mapping recorded.
- **M2.** apk-era upgrade + reboot scenario in `tests/scenarios/`
  (`test_install_paths.py` scenario A′) and the installer-contract lane
  (`tollgate-module-basic-go` PR #892) merged and run per release
  candidate — together these supersede the fleet's install coverage on
  25.x.
- **M3.** Fold `tests/stories/` into the `tests/scenarios/` + markers
  scheme; reduce `scripts/release-test.sh` to a thin profile over
  `run-profile.sh` / `test-pr.sh`.
- **M4.** Delete the main-repo legacy fleet files once every mapping row
  above has met its deletion gate; finish the `mint-health/` and
  `upstream-wifi/` Makefile → pymake migration and delete the legacy
  Makefiles.
