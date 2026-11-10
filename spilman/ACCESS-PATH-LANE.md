# Access-path lane — does a channel's credit actually buy access?

The protocol lane (`spilman_router_lane.py`) proves **open → pay → settle →
restart** on a physical router. It does **not** prove the thing the feature is
named after: that a client is served bytes *because a channel paid*.

That distinction is not academic. During this work the rail was found sitting
**off the access path**: `authorize_bytes` was called only from
`/spilman/status` and tests, while `terminal_sessions` decided access purely from
`CustomerSession.allotment`. Every protocol test passed. A channel bought a
client exactly nothing.

This lane closes that gap.

---

## What it runs

`access_path_lane.py` deploys **one** binary (`access_credit_probe`) to the
router, verifies its sha256 **on the device** against the host before executing
it, runs it, and removes it.

The probe drives the module's own decision functions —
`session::channel_credit::{effective_allotment, sweep_with_channel_credit}` —
against a real `SpilmanRail` on a real ledger file. It does **not** re-implement
the arithmetic: a re-implementation would prove only that the probe agrees with
itself.

## Assertions

| # | Case | Why it matters |
|---|---|---|
| 1 | rail off ⇒ retail allotment unchanged | regression guard for "no channel, no change" |
| 2 | verified channel credit raises the ceiling | a payment must actually buy something |
| 3 | exhausted channel credit ⇒ session terminal | credit must stop buying at the limit |
| 4 | a later balance extends the SAME session, keeping `used` | increments work without a re-grant |
| 5 | an UNKNOWN channel adds nothing | no verified balance ⇒ no credit |
| 6 | bytes-per-unit matches the rail's own rate | the access path and `authorize_bytes` cannot disagree |

**Cases 3 and 4 assert their own preconditions.** They would otherwise pass
trivially when the channel bought nothing (`used == allotment` is true when
`allotment` is 0). A regression that broke the credit path must not be able to
hide behind an empty fixture.

## Running it

```bash
export SPILMAN_HOST_IP=<bench-host address the router can reach>
export SPILMAN_PROBE_DIR=~/artifacts-spilman/deploy5   # holds access_credit_probe
python3 spilman/access_path_lane.py
```

Build the probe (from the module repo):

```bash
cargo build --release --target aarch64-unknown-linux-musl \
  --features test-support --example access_credit_probe
```

**`SPILMAN_HOST_IP` is required and is NOT the default-route address.** See the
`--host` section of `README.md`: two attempts to auto-detect it were made and
both withdrawn after measuring. Guessing wrong fails with an empty checksum, not
a clear error.

## Mutex discipline

Identical to the protocol lane, via its own `acquire_bench()`: serial lock +
shared bench lock + `/proc` fd scan, taken **before** the port opens. BUSY =
skip and report, never queue. The run is non-invasive — private `/tmp` dir,
nothing stopped, no config written, no binary replaced — and it removes what it
created.

## Result on the bench

`tollgate-0195`, aarch64 musl, reproduced twice:

```
PASS rail off keeps the retail allotment (1000)
PASS unknown channel adds no credit (retail kept: 4242)
PASS a verified balance was recorded
PASS verified channel credit raises the ceiling (1000 + 22021000 = 22022000)
PASS conversion uses the rail's rate (22021000 bytes/unit at 22021 bytes/msat)
PASS exhausted channel credit revokes at the ceiling (22021000)
PASS a later balance extends the same session (used kept at 10)
ACCESS_PATH_OK
```

## What this still does NOT prove

- **No traffic through nodogsplash.** This proves the *decision*; the link from
  "the sweep says terminal" to "the gate actually closes" is the repo's existing
  mechanism, unchanged and not exercised here.
- **Granularity.** The check runs on the 30 s sweep, so an exhausted channel keeps
  a gate open for up to 30 s. MONAD checks per chunk inside the data path. That
  difference is real and documented in the module PR rather than glossed.
- **Two routers.** Still blocked: the second board is mips and the Rust toolchain
  has no mips target.
