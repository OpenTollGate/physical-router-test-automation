# Testing cycles — rails, measured times, and the state of the art

> Written 2026-10-14 after the v0.6.0 rc4/rc5 validation campaign. Numbers are
> measured, not estimated. Companion to `AGENTS.md` (2026-10-13 session section)
> and `docs/venue-networks.md`.

## The rails

| Rail | What it proves | Cycle (measured) | Status |
|---|---|---|---|
| **Virtual venue** — x86_64 QEMU on an isolated bridge (br-laneA pattern, ai-legion-small) | install, health, payment semantics, portal endpoints | bring-up ~2 min · local build ~8 min · deploy+gate ~2 min · payment suite 16 s → **feed-tag→verdict ≈ 15 min** | ✅ proven on rc4-era and rc5 |
| **Official installer path** — `install-and-test.sh` headless against a fresh venue VM | the exact flow a user runs: wizard → feed package → brand → portal → LNURL → health | ~5 min | ✅ proven (pinned pre-parking tag); its verify bug fixed by our tollgate-installer#133 |
| **Full tier sweep** — poc lab (ai-legion), `run-local-tests.sh`, ~94 tests | broad API surface, token-format matrix | ~20–40 min when the host is healthy | ⚠️ blocked by host memory contention; #241's preflight now fails fast instead of wedging |
| **Physical feed-served DUTs** (GL-MT3000/MT6000 class, aarch64) | official path on real flash/radio | est. ~30 min/story-suite once seated | ❌ awaiting hardware seating |
| **Physical local-build DUTs** (COVR-X1860, mipsel — feed-parked) | same code on second arch | est. ~15 min (build+deploy+suite) | ❌ awaiting bench power |
| **Phone stories** (shared moto g via labgrid) | the real captive-portal UX | ~30–60 min full suite | ❌ needs the `numo-phone` place acquired + on-device USB-debug auth |

## The release-day loop (today)

```
feed watcher (30-min poll, #252)
  → new tag
  → local x86_64 build from the tag's source commit (~8 min, Node 22.17.0 + go1.26.8 pins)
  → venue ensure (~2 min, persisted image = one systemd-run command)
  → deploy + health gate (~2 min — NDS binary, ndsctl, kind:10021 ad)
  → payment rejection suite (16 s — PRTA#240: no-consume rejection, kind:1022,
    double-spend guard)
  → verdict
```

Total: **~15 minutes, unattended-able**. The rc5 run (2026-10-14) was exactly
this and caught the version-stamp divergence (tmbg#941) on the way in.

## What "state of the art" looks like — and where we stand

1. **Trigger → verdict without humans.** We have it for the virtual rail
   (watcher + scripted venue). Gap: the loop is four commands, not one —
   `scripts/release-validate.sh` collapses it (backlog #1).
2. **Artifact fidelity.** The thing under test should be the thing users
   install. Today x86_64/mipsel are parked from the feed (installer#132), so
   virtual runs validate a byte-identical *rebuild* — same source, different
   artifact. Unparking makes every virtual run feed-faithful AND removes the
   8-minute build step.
3. **Second-arch + physical coverage.** Release sign-off needs the official
   path on at least one feed-served physical device. The bench has seats
   (see the seating plan in this doc's discussion) — this is a cabling task,
   not a software one.
4. **Evidence-first merging.** PRs carry live-validated evidence (the #240
   test was run against the venue before opening; #930 ships a live repro).
   Review-gated repos (tmbg) are correct to hold merges for humans.
5. **Behavior contracts, not snapshots.** Pinned semantics
   (client-not-registered no-consume, double-spend codes) live as regression
   tests; drift shows up as diffs, not surprises.
6. **Honest failure modes.** Wedged VMs fail gates loudly (#237), starved
   hosts refuse to boot labs (#241), week-old junit can't pose as fresh
   (#237). Every rail bug found this campaign became a guard.

## Improvement backlog (ranked)

1. `scripts/release-validate.sh` — one command for the whole loop (~30 min work).
2. Feed unpark x86_64+mipsel (installer#132) — kills the build step, makes
   runs feed-faithful (maintainer decision).
3. Physical seating: MT3000 → bench-owrt free bay (VLAN already trunked);
   x1860s → their existing lan4/lan5 seats. Then extend the br-laneA venue
   pattern to per-DUT bridges (venue-networks.md step 4).
4. Portal Playwright specs against the venue (:2051 is already routed).
5. PR CI lane for PRTA (shellcheck + unit) — would have caught a printf
   escape bug pre-merge (#241's fixup commit).
6. Phone: acquire `numo-phone` place + on-device USB-debug auth → story suite.
