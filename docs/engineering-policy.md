# Cross-repository engineering policy — ownership and verification

Status: **v1, 2026-10-13.** Authoritative for every OpenTollGate repository
and the FreedomTechFeed shipping feed. Humans and AI coding agents are both
bound by it. Change it by PR with cross-repo review; do not fork it into
other repositories — link here.

Sibling decision records, preserved and not superseded by this document:
[authoritative-test-suite.md](authoritative-test-suite.md) (PRTA as the
behavior-contract authority, runner consolidation M1–M4) and
`tollgate-module-basic-go:docs/release-process.md` (the release runbook).

## 1. Ownership map — one owner per responsibility

| Responsibility | Owner | Everyone else |
| --- | --- | --- |
| Go runtime implementation, its unit tests, source-level contracts | `OpenTollGate/tollgate-module-basic-go` | Rust/ESP32 implementations own their own runtime + unit tests the same way |
| Cashu wallet layer (gonuts fork) | `OpenTollGate/gonuts-tollgate` | Wallet fixes land there first, are tagged, then bumped via the `replace` directive |
| Wire-protocol specifications (TIPs, HTTP/NOSTR/WIFI) | `OpenTollGate/tollgate` | Implementations conform; they do not redefine the protocol |
| Captive-portal application, assets, portal contracts | `OpenTollGate/tollgate-captive-portal-site` | Consumers pin a revision (`packaging/build-inputs.json`); never carry divergent copies |
| **Shipping OpenWrt packages** (recipes, pins, deps, published `.apk`/`.ipk`, feed index) | `FreedomTechFeed/packages` | Implementation repos keep reproducible **development** build paths only (`scripts/build-sdk-package.sh`); they are not competing shipping authorities |
| **Router integration & deployed-artifact acceptance testing** (virtual OpenWrt, physical routers, bench orchestration, user journeys, release-acceptance evidence, cross-implementation contracts) | `OpenTollGate/physical-router-test-automation` (PRTA) | Implementation repos keep unit tests + source contracts; router-visible verification belongs here |
| CI scheduling/enforcement (ngit lane) | `.ngit/act/workflows/` in each repo + `OpenTollGate/.github` | CI triggers **existing** entry points and enforces their results; it never reimplements deploy/install/package/test logic |
| Release authorization | a human operator, per `docs/release-process.md` | Agents prepare evidence and stop at the operator gates (tag push, publication) |

If a change needs two of these owners, it is one logical change per
repository, coordinated in both PR descriptions.

## 2. Verification hierarchy

Higher levels carry more truth; **no level's result may be described as
proving a higher level's behavior.** Era discipline everywhere:
24.10/opkg/`.ipk` and 25.12/apk/`.apk` are different environments; a
simulated package-manager action never substitutes for the real one.

**Level 1 — source-level** (owner: implementation repo; fast, deterministic)

- `tollgate-module-basic-go`: `make go-battery` (16 Go modules: gofmt/vet/
  build/`go test -race -tags testenv`), `node tests/contract/js-schema-lint.mjs`,
  `bash tests/contract/build-purity.sh`, `bash tests/contract/check-ssid-format.sh`,
  `tests/packaging/*_test.sh` + `tests/uci-defaults-*_test.sh`, and
  `tests/installer-contract/selftest.sh` (offline planted-regression controls).
- Other implementations: their own unit suites at their repo root.

**Level 2 — virtual integration** (real artifacts, real package managers, VMs)

- Pristine apk-era install contract (25.12):
  `tollgate-module-basic-go:tests/installer-contract/run.sh` — fail-closed
  (`NOT_VERIFIED` ≠ PASS), binds artifact sha256 + source SHA, re-verifies
  after reboot.
- Published-artifact happy path (userspace):
  `tollgate-module-basic-go:tests/happy-path/run.sh`.
- Deployed development VMs: PRTA `scripts/virtual-lab.py` (dev iteration),
  `scripts/cloud-lab.py submit` (SHC, fire-and-forget).
- Development artifact builds: `scripts/build-sdk-package.sh` (fail-closed
  staging preflight). **Shipping artifacts come from FreedomTechFeed.**

**Level 3 — physical integration** (real routers/radios/clients)

- PRTA is the only venue: `make lock`, labgrid places
  (`LG_COORDINATOR=ai-legion:20408`), `tests/scenarios/ -m hardware`,
  `scripts/test-pr.sh`, `scripts/install-path-e2e.py` (fresh-flash install
  paths), the bench lock and device-identity rules in PRTA's `AGENTS.md`.
- `tollgate-module-basic-go:tests/router-happy-path/run.sh` is the module
  repo's read-only artifact-identity companion on hardware; it asserts, it
  does not deploy.

**Level 4 — release acceptance**

- Evidence must name: implementation commit SHA, test-framework commit SHA,
  artifact SHA256, OpenWrt version + arch + package manager, executed tests,
  and outcomes. Outcomes are `PASS` / `FAIL` / `SKIP` / `NOT RUN` /
  `INFRA ERROR` — a required check that was skipped, unavailable or never
  run is **never** a release PASS.
- Existing producers: `tests/installer-contract/run.sh` (`verdict.json`,
  `ICCHECK/ICEXIT` idiom), `scripts/verify_publication.sh` (publication
  completeness only — presence/hash, not behavior),
  `scripts/release-check.sh` (maintainer pre-release gate; conformance lane
  SKIPs unless required by env), PRTA run directories (`results/<run_id>/run.json`).
- An operator authorizes the release (`docs/release-process.md` §0 gates).

## 3. AI-agent operating principles

Before implementing: identify the authoritative owner (§1); search existing
implementations, decisions and **open PRs** for the same change; reproduce
or at least precisely cite the observed problem and its target environment;
select an existing canonical runner before proposing any new mechanism;
state observable acceptance criteria.

While implementing: smallest correct change; preserve externally observable
contracts unless authorized; no competing writers of runtime state; no
duplicate declarations of versions/ports/dependencies/package contracts —
reference the one owner or generate derived copies in one run; never modify
a DUT to make a test pass; harness config stays separate from product
config; no new framework/abstraction/doc when existing infrastructure
suffices; one active implementation PR per problem.

While verifying: reproduce the original failure; test the actual changed
behavior; run Level 1 in-repo; request/perform PRTA integration when
behavior is router-visible; name every unexecuted hardware check; never say
a test passed unless you executed it; never read a skip as success; verify
the exact artifact, not an unrelated local build; preserve failure evidence
— weakening an assertion to go green is forbidden.

Before proposing merge, state: what changed and why; which authoritative
contracts were affected; whether another repository must change; which
tests ran (with evidence locations); what remains unverified; whether the
tested artifact matches the reviewed commit; whether the change adds a new
owner, runner or duplicated responsibility.

Safety is unchanged and absolute: money-safety, router-safety, secrets and
lab-isolation rules in each repo's `AGENTS.md` still govern; physical
flashing, destructive resets, real-fund operations and public publication
keep their explicit operator approvals; never bypass a lock or safety check
for a green result.

## 4. Migration status (do not relitigate, do not mark incomplete work done)

> Live status — the per-workstream board, the PR queue and the milestone
> sequencing — lives in [consolidation-map.md](consolidation-map.md).
> The table below records the states this policy was written against.

| Item | State |
| --- | --- |
| Runner consolidation M1 (PRTA #216) | **merged** — blocking verdicts, shared deploy path, fleet migration audit |
| Install moves router off LAN → fail fast (PRTA #217) | **merged** |
| Hardware-fleet retirement docs (module #916), installer-contract gate (module #892), staging preflight (module #918) | **open PRs, pending review** — described as proposals until merged |
| Deployed-artifact contract tests (PRTA #196), topology stop-ship scenarios (PRTA #201) | **open proposals** |
| Fleet deletion + M2–M4 milestones | **pending** — legacy tests are deleted only after their canonical replacement demonstrated equivalent coverage in the relevant environment |
| Module-repo shipping-package decommission (#914 direction) | **proposed** — FreedomTechFeed is the shipping authority in this policy; the duplicate path's removal is a separate, explicitly-planned change |

## 5. Documented policy vs executable enforcement

This document changes what agents are *told*. It does not itself make
anything enforceable. Known gaps, priority order:

1. The live ngit publication lane gates on `release-guard` only — no
   behavioral test blocks `announce` (the dead GitHub lane had a fail-closed
   happy-path edge that was never ported).
2. PRTA pytest suites still report skips as green at the job level; only the
   installer-contract lane implements `NOT_VERIFIED` fail-closed semantics.
3. No cross-repo fence against duplicate declarations (port tables, version
   pins, package contracts) beyond the in-repo ones
   (`check-entry-ui-ports.sh` pattern).
4. No mechanical "one active PR per problem" detection.
5. Release-acceptance evidence is not yet machine-collected per release
   (Level 4 producers exist; no single collector).

## 6. What this policy does not do

It does not change any runtime behavior, CI job, test runner or packaging
script; it does not reopen settled product decisions; it does not remove
legacy infrastructure; and it does not claim enforceability — see §5.
