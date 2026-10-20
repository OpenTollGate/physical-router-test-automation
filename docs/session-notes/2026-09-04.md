# Session Notes — 2026-09-04 (CI triage + local-lab-green)

Follow-up to SESSION-NOTES-2026-09-03.md (priority queue items 1–4 worked).
Branches only for tmbg (rule respected); tmb-rust PR merged after CI green.

## Priority 1 — tmb-rust CI triage: FIXED + MERGED ✅

Commit 779a3a2 (cdk 0.18 bump) CI failures root-caused from job logs (not
guessed):

1. **cross mips/mipsel E0432** — `std::sync::atomic::AtomicU64` unresolved at
   `src/session/mod.rs:11` — the project's OWN code, NOT cdk. cdk-common
   v0.18.0 compiled clean in the same logs (the dropped [patch.crates-io]
   pin was NOT needed; 0.18 is genuinely AtomicU64-free). mips32 has no
   native 64-bit atomics (aarch64/armv7/x86_64 pass). `AtomicUsize` is NOT a
   substitute: `last_save_ms` holds epoch-millis (~1.75e12) → 32-bit
   overflow.
   - Fix: `Mutex<u64>` with poison-tolerant `into_inner` (SessionManager
     always lives behind Arc<Mutex<…>>; contention irrelevant).
   - PR #2: ALL checks green in real CI (incl. both MIPS cross jobs).
     **Merged to main → `b3dcff0`** (mission gate "main after green" met).
   - Local verification recipe (no CI needed next time):
     `cargo +nightly check -Z build-std=std,panic_abort --target
     mipsel-unknown-linux-musl` with the rustp2p/musl-cross
     `mipsel-linux-muslsf-cross` toolchain + `cp libgcc_eh.a libunwind.a`
     in its gcc dir (exactly what the workflow does). Toolchains cached at
     /tmp/opencode/prta-1530/musl-tc/ (this boot only).

2. **"Rust basic + PRTA tests" KeyError: 'request'** — PRTA test bug, not
   backend: `test_ln_invoice_creates_real_quote` asserted
   `data["request"]`, but rust-basic's LightningInvoiceResponse emits
   `invoice`. Stub-degraded path (mint HTTP 500 in CI) returns
   `quote="stub-quote-N"` + `invoice="stub-invoice"` → old test KeyError'd
   on EVERY real quote. Also `test_ln_invoice_status_check` asserted
   Go-style `checkState` (never emitted by rust-basic).
   - Fix: prta `fix/rust-basic-ln-invoice-schema` (15eab12) — stub detection
     keys off quote/invoice startswith("stub"); checkState assertion dropped.
     Verified locally with the EXACT CI command against a release build:
     22 passed / 5 xfailed (incl. a real testnut payment).
   - **Landed on Amperstrand/prta main via fast-forward** 6f1cfb4→15eab12
     (fork main was 14 commits behind origin/main, zero divergence — CI now
     tests the current suite).

## Priority 2 — tmbg fork apk packaging: FIXED on PR #86 ✅ (awaiting review)

Runs 33799465731/33799465935 — two independent causes, both fixed on branch
`fix/ci-apk-packaging` (22ea0dd + f9e95b8):

1. **package-apk (all 4 variants)**: `apt-get install -y nodejs npm` inside
   the openwrt/sdk bullseye container → Node 12.22; vite build dies
   (`SyntaxError` on `module.enableCompileCache?.()`; portal needs ≥20.19).
   package-ipk passed because it uses actions/setup-node on the host runner.
   Fix: actions/setup-node@v4 node 22 (mirrors ipk job + test.yml).
2. **deps-and-imports**: gonuts-tollgate drift (root+tollwallet v0.11.1 vs
   cli+merchant v0.10.0). `go get v0.11.1` + tidy in both modules. Verified:
   check-deps-sync 124 deps ✅; go build+test green in merchant (190s) + cli.

**PR #86 CI: fully green** — all apk/ipk jobs + publish-metadata → **fresh
x86_64 ipk/apk artifacts exist on Blossom** (kind 1063, branch
fix/ci-apk-packaging). NOT merged (tmbg rule: no main merges without
review). Note: pushing the branch required SSH remote (OAuth token lacks
`workflow` scope — use `git push git@github.com:Amperstrand/...` for
workflow-file changes).

## Priority 3 — SHC full-suite: BLOCKED by SHC Dev-zone outage (again)

- Submit pipeline proven end-to-end: `--branch fix/ci-apk-packaging --commit
  f9e95b8 --repo Amperstrand/tollgate-module-basic-go` resolved the fresh
  artifacts **from Blossom** ("Artifact ready: run blossom"), ordered
  tollgate-f9e95b8-63934Z (svc 2415, $0.46/day, balance $62.52).
- **64.188.7.0/24 unroutable from Europe** — shc-toolkit#27/#28 recurring
  (2026-09-04): VM active/provisioning, IP assigned, but TCP/22 + ICMP 100%
  loss from this host while the Blesta API stays reachable. Bootstrap never
  ran; self-destruct never armed; **svc 2415 cancelled in-session** ($0.45
  refund). No key material reached the VM.
- Lesson re-confirmed: full-suite (nested KVM) needs Dev tier; Katy NVMe has
  no /dev/kvm (also #27). SHC must expose Dev in a reachable zone — else
  this stays blocked regardless of artifacts.
- Gotcha fixed en route: `--pr 86` resolves against the DEFAULT repo
  (OpenTollGate), where #86 is an unrelated old PR → use `--branch/--commit
  --repo <fork>` for fork PRs.
- Basic-auth note: on THIS account (eddy-e2e profile), the full nomail email
  works as Basic username (HTTP 200); bare local part 401s. Lesson 26's
  bare-Blesta-username rule applies to the CI account, not this one.

## Priority 4 — local-lab-green

- `fix_nodogsplash_auth_marks()` added to lib/router.py (branch
  `fix/local-lab-green` be7a309 + d7de18c cherry-picked as 2dc1122):
  rewrites NDS 5.0.2's 0x30000 ndsOUT rules to 0x20000 (delete + re-insert
  at 1, specs derived from `iptables -t mangle -S ndsOUT` so the delete
  matches exactly). Optional ip/mac filter. 4 unit tests; full tests/unit
  green (424 passed).
- d7de18c (port-aware mint blocking + self-bounding swap test + notice
  alignment) now on `fix/local-lab-green` — degraded cluster fixes apply on
  the branch (A/B: main baseline 77 passed/26 failed → branch clean run 105
  passed/18 failed/54 skipped).
- ⚠️ **New trap discovered**: a SIGKILL'd run-local-tests (tool timeout
  during run2) leaves NDS on the OpenWrt VM wedged — `ndsctl json` gets
  SIGKILLed by the backend's valve every 5s ("signal: killed", 203×). The
  next suite inherits the wedge: payments/degraded/mint tests cascade-fail
  (31 failed / 91 skipped vs clean 18/54). **Fix before any suite run:
  `/etc/init.d/nodogsplash restart && /etc/init.d/tollgate-wrt restart` if
  `ndsctl json` doesn't return valid JSON.**
- 🔬 **THE local-lab root cause found (live-probed, fixed in 90bf9c6)**:
  the runner wipes the mint work-dir every start; the Go wallet keeps
  stale mint state in wallet.db → discovery serves kind 21023
  "no-reachable-mints" while the prober reports ok=true and curl from
  router+host returns 200. `run-local-tests.sh` now restarts
  tollgate-wrt after mint health (resync_backend_wallet()). ALSO: mint
  health transitions ride the backend's 300s proactive poll — 180s test
  budgets expired pre-probe (HEALTH_POLL_TIMEOUT→420, module timeout
  →900). Result: merchant_provider alone went 1F/1P/4S → **3 PASSED**.
  Remaining 3 failures = backend design (recovery after repeated block
  cycles exceeds any budget; escalating backoff not reset on unblock) →
  **filed Amperstrand/tollgate-module-basic-go#87**.
- Suite numbers context: run1 (fresh boot) 105/18/54; run3 (NDS wedge
  legacy) 127/31/91; run4 (wedge cleared, but wallet-staleness +
  poll-cadence failures) 268 PASSED/40 FAILED/182 SKIPPED — the 40
  cluster decomposes into: balance_page/uhttpd env cluster (5, known),
  wallet-staleness degraded cluster (fixed by resync), poll-cadence
  transitions (fixed by budgets), repeated-cycle recovery (issue #87),
  and cdk-0.18 `UNIQUE constraint failed: blind_signature.blinded_message`
  → "Duplicate outputs" swap rejections + "Token Already Spent" (mint log,
  /tmp/cdk-mintd-local.log — concurrency collisions, knowledgebase#1
  items 3/4 territory).
- Runner gap noted: pytest stdout buffers under nohup → failure tracebacks
  + `-rs` skip reasons lost on crash; next session add `--junitxml` +
  `-rA` to the runner invocation.
- tmb-rust ln-invoice test fixes above ALSO count here (CI env surfaced
  them; local rust-basic suite now 22 green).

## Priority 5 — knowledgebase#1

Scoped only (lab busy): item 2's strict `method`-field parser tests matter
for gonuts (Go) + cashu-ts consumers; PRTA's HttpMinter is naturally
tolerant (keyed access + .get — extra 0.18 fields `method`, `amount_paid`,
`amount_issued`, `updated_at` don't affect it). Item 3's swap-test rewrite
is DONE via d7de18c (self-bounding deadline) — issue text can be updated.
Keyset-expiry + pacing-burst experiments still need a free lab window.

## Branch index (additions today)

| Repo | Branch | State |
|---|---|---|
| tollgate-module-basic-rust | (merged) | main @ b3dcff0, CI green |
| physical-router-test-automation | fix/rust-basic-ln-invoice-schema | ON amperstrand main (15eab12) |
| physical-router-test-automation | fix/local-lab-green | be7a309+2dc1122+90bf9c6, pushed |
| tollgate-module-basic-go | fix/ci-apk-packaging | PR #86 green, artifacts on Blossom, awaiting review |
| tollgate-module-basic-go | (issue) | #87 filed: prober/wallet split + poll-driven health + backoff |

## Resource state at handoff (to be updated after run4)

- Disk ~31G free. Lab running (run4); stop with
  `python3 scripts/virtual-lab.py stop-poc --host localhost` when done.
- PR #2 branch can be deleted post-merge; musl toolchains in /tmp are
  boot-scoped.
