# Physical-router lane — Spilman channel

Tests the Spilman channel rail on the **real bench router**. Upstreamed so it can
be reused, and so the E2E claim is checkable by someone else.

**Mutex discipline is the point of this lane.** The bench is shared with other
context windows, so the lane takes the lock **before** it opens the port and
**skips rather than queues** if the bench is busy. An earlier attempt at this work
hung for 7 minutes on the wrong serial port and interrupted a sibling window; this
lane is written to make that failure impossible.

---

## What it asserts

| # | assertion | kind |
|---|---|---|
| A | the module binary runs on the router and serves `/whoami` | **achievable now** |
| B | `/spilman/params` answers on the router when enabled | **achievable now** |
| C | a forged payment is REFUSED on the router | **achievable now** |
| D | `/spilman/*` 404s on the router when the rail is disabled | **achievable now** |
| E | channel opens against the test mint | **SKIP — blocked** |
| F | zero mint calls between open and settle | **SKIP — blocked** |
| G | settle pays the payee exactly | **SKIP — blocked** |
| H | restart mid-stream keeps the latest signature | **SKIP — blocked** |

### Why E–H are skipped, not silently passed

A genuine channel payment is currently **refused by design**. The payee cannot
verify a signature until it persists a per-channel funding record and calls
upstream's validator (`SpilmanBridge::validate_payment`). Until that lands, the
arm is closed — so a real payment is refused too, and E–H cannot pass.

Reporting them as SKIP-with-a-reason is deliberate. A lane that reported "pass"
here would be lying about the goal's central claim.

---

## Mutex contract (non-negotiable)

| Lock | Guards |
|---|---|
| `~/.local/state/tg-bench/tollgate-A04E.lock` | the A04E serial console |
| `~/.hermes/state/bench-mt3000.lock` | the shared bench as a whole |

Rules, each earned from a real incident:

1. **`acquire()` runs before anything opens the port.** A `/proc` scan is a
   *check*, not a lock — it tells you about other processes and grants no ownership.
   The discriminating question is not "did you check?" but "is `acquire()` in the
   code path that opens the port?"
2. **The `/proc` fd scan is authoritative over `flock`.** Measured 2026-10-17:
   `flock` reported FREE while a foreign process held the tty, because that process
   never took the lock. A flock-only guard passes exactly the case that corrupts
   another window's session.
3. **BUSY = skip and report, never queue** (exit 3). Queueing silently behind
   another owner on single-owner hardware is how two "sequential" runs interleave.
4. **Children must not inherit the lock fds** (`8>&- 9>&-`).
5. **Identify the board by its PROMPT, never by the port.** The FTDI↔tty mapping
   drifts; a wrong port also *interrupts* whoever is on the right one.
6. **A wall-clock alarm** so a wedged shell cannot pin the bench forever.

The guard is itself tested (`test_lane_guard.py`) — a guard nobody tested is a
rumour:

```
case 1 (serial flock held): rc=3 PASS
    SKIP: ... tollgate-A04E.lock is held by another owner. Not queueing.
case 3 (free): rc=1 PASS — guard did not block
RESULT: all guard cases behaved correctly
```

---

## Transfer path: HTTP, not serial

The module binary is ~12–19 MB. The console wraps at ~80 characters. Serial is used
**only** for short commands and identity probes; artifacts move over HTTP
(`python3 -m http.server` on the bench host, `wget` on the router).

---

## Usage

```bash
bash spilman/bench-setup.sh          # LAN-bound test mint + artifact server
python3 spilman/spilman_router_lane.py --port /dev/ttyUSB0
bash spilman/bench-teardown.sh
```

`--dry-run` takes the locks, confirms board identity, and stops before mutating.

---

## Evidence from the first real run

Router: `tollgate-0195`, OpenWrt 24.10.2, aarch64 musl, serial `/dev/ttyUSB0`
@ 115200, identified by prompt.

```
$ sha256sum libcdk_spilman_go.so spilman_probe        # on the router
dbf2d1d4ad4df56075e21f265d0e2aeeebd6c34fe5803a61716db6a20d934b5a  libcdk_spilman_go.so
d4d1144823d51f31def39ba995c47eff24b8cbe38a4e00af07c25bdc9b9537fa  spilman_probe

$ LD_LIBRARY_PATH=/usr/lib:/lib /tmp/spilman_probe
spilman-go static link smoke. library keyset versions: [v1 v2]
probe_exit=0
```

The router's own checksums **match the host's**, so the bytes that ran are the bytes
built. `ldd` resolves every NEEDED entry on the device.

**Not touched:** no service stopped, no config written, no existing binary replaced.
The incumbent `/usr/bin/tollgate-wrt` (another context window's build) and
`/etc/tollgate/mint-rt` were left exactly as found. Only `/tmp` artifacts, plus a
transient `/usr/lib` `.so` removed in the same run.
