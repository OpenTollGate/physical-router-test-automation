# Spilman channel bench lane

Infrastructure-as-code for testing a **Spilman payment channel** between a TollGate
router and an upstream, on the physical bench.

This lane exists because Spilman's whole value proposition — *"buy internet in
small increments without a mint round trip per purchase"* — is a claim about
**network behaviour under a running router**, and nothing short of a physical
device can falsify it. A host-only test proves the arithmetic; only the bench
proves the channel survives the router's real metering loop.

---

## Why the bench is shaped this way

A Spilman channel needs a **Cashu mint the router can reach**. The router does not
run one and cannot run one usefully:

- the mint must be reachable by *both* parties, and
- the router has ~3 MB of flash headroom and no `curl`/`sqlite3`.

So the topology puts the mint **off** the router, bound to the LAN:

```
┌────────────────────────────────────────────────────────────────────┐
│  BENCH HOST (LAN-reachable; the router must reach this address)     │
│                                                                     │
│  cdk-spilman-test-mintd  ── binds 0.0.0.0:3338                      │
│    fakewallet + sqlite; REAL cdk::Mint                              │
│                                                                     │
│  python3 -m http.server :18999 ── serves the cross-built module     │
│    binary for the router-side `wget` (file transfer, not serial)    │
│                                                                     │
│  lane driver (payer side) ── opens + signs + settles the channel    │
└────────────────────────────────────────────────────────────────────┘
                                  ▲
                                  │  HTTP (mint + module), LAN
                                  ▼
┌────────────────────────────────────────────────────────────────────┐
│  ROUTER (tollgate-A04E, aarch64 musl)                               │
│  runs the module; hosts the payee-side channel server               │
└────────────────────────────────────────────────────────────────────┘
```

**The mint bind is load-bearing.** `cdk-spilman-test-mint` defaults to
`127.0.0.1` (`crates/cdk-spilman-test-mint/src/lib.rs:72`) and both upstream launch
scripts hardcode loopback. Started that way, the **host** sees a healthy mint while
the **router** sees nothing — a silent, confusing failure. This lane always starts
it with `--listen-host 0.0.0.0` and a matching `--base-url`.

**Payments travel over HTTP, not serial.** A steady-state `Payment` is ~310 base64
characters (64-char channel id hex + 128-char BIP-340 signature hex); the funding
first payment is several KB. The console wraps at ~80 characters. Serial is used
**only** for identity probes, `wget` fetch triggers, and short restarts.

---

## Files

| File | Purpose |
|---|---|
| `bench-setup.sh` | Idempotent: build + start the test mint on the LAN, start the module HTTP server |
| `bench-teardown.sh` | Reverse of setup; keeps mint data unless `--purge` |
| `spilman_router_lane.py` | End-to-end lane against the physical router over serial |
| `mint_proxy.py` | Counting HTTP proxy in front of the mint — the "zero mint calls" measurement |
| `test_lane_guard.py` | Negative tests for the bench guard, incl. "kill only our own pid" |
| `test_lane_parse.py` | Unit tests for console parsing, pinned to real captured transcripts |
| `ROUTER-LANE.md` | The lane's contract, evidence, and how to read a SKIP vs a FAIL |
| `README.md` | This file |

---

## Mutex discipline (non-negotiable)

This bench is shared with other context windows. The lane takes **two** locks and
refuses to run if either is busy.

| Lock | Guards |
|---|---|
| `~/.local/state/tg-bench/tollgate-A04E.lock` | the A04E serial console |
| `~/.hermes/state/bench-mt3000.lock` | the shared bench as a whole |

**The `/proc` fd scan is authoritative, not `flock`.** Measured 2026-10-17: `flock`
reported FREE while a foreign process held the tty, because that process never took
the lock. Three checks together give a defensible verdict:

1. `/proc` fd scan for anything holding `ttyUSB*` — **any hit means stop**
2. `flock -n` on the lock file
3. no competing `console-exec|console-probe|router-console|live_harness|tio` process

If they disagree, believe the fd scan.

**BUSY = skip and report, never queue.** Queueing silently behind another owner on
single-owner hardware is how two "sequential" runs interleave.

**Lock first, in the code path that opens the port.** A `/proc` scan is a check,
not a lock. The discriminating question is not "did you check?" but "is
`acquire()` in the code path that opens the port?"

**Children must not inherit the lock fds** (`8>&- 9>&-` on any spawned daemon) —
the hedgehog lane's bitcoind-inherits-the-lock incident.

---

## What the lane asserts

One router, one channel, one direction, no roaming:

- (a) the channel **opens** against the test mint
- (b) **zero** mint HTTP calls between open and settle — asserted from the mint's
  own request log, not from our side's opinion
- (c) **settle pays the payee exactly** the signed balance
- (d) **kill-restart mid-stream does not lose the latest signature** — after a
  restart the status still reads the same balance, and the remaining steps succeed

## What the lane SKIPS, with a named reason

| Skipped | Reason |
|---|---|
| Two-router lane | only one router on the bench |
| Roaming / upstream switch | needs two upstreams; channel-stranding design is unresolved |
| Keyset rotation mid-channel | needs mint-side rotation control the lane does not have |
| mips targets | no Rust std for stable; no mips musl toolchain. See the Go gap analysis §3 |

A skip is reported as a skip with its reason. It is never silently reported as a pass.

---

## Usage

```bash
# 1. provision (mint on 0.0.0.0 + module server)
bash spilman/bench-setup.sh

# 2. run the lane against the router
python3 spilman/spilman_router_lane.py --port /dev/ttyUSB0

# 3. tear down
bash spilman/bench-teardown.sh
```

Identify the board by its **prompt**, never by the port — the FTDI↔tty mapping
drifts. Probe both `ttyUSB0` and `ttyUSB1` and match the hostname.
