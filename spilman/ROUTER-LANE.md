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
| A | the deployed module runs on the router and serves `/whoami` | runs now |
| B | `/spilman/params` answers on the router when the rail is enabled | runs now |
| C | a forged payment is REFUSED on the router | runs now |
| D | `/spilman/*` 404s on the router when the rail is disabled | runs now |
| E | channel opens against the test mint | protocol, SKIP until the driver exists |
| F | **zero mint calls between open and settle** (measured) | protocol, SKIP until the driver exists |
| G | settle pays the payee exactly | protocol, SKIP until the driver exists |
| H | restart mid-stream keeps the latest signature | protocol, SKIP until the driver exists |

Last real run: **7/7 achievable assertions PASS, 4 SKIP with a named reason.**

---

## The deploy step (why the lane owns its own artifacts)

The lane pushes the built **module binary** and the **`spilman-payer` driver** to
the router itself and verifies `sha256` **on the router** against the host, so the
bytes that ran are the bytes that were built. Transfer is HTTP — `python3 -m
http.server` on the bench host, BusyBox `wget` on the router. Serial is used only
for short commands and identity probes; the console wraps at ~80 columns and the
binary is ~8 MB.

### The private-port rule (never bind :2121)

`:2121` belongs to another context window's module, and `:2122` is the port a
sibling window used in `device-test-cmds.txt`. The lane therefore:

- refuses to start if `--lane-port` is in `{2121, 2122}` (hard abort, not a warning);
- runs its module on a **private port** (default `:2123`) and a **private state
  dir** (`/tmp/spilman-lane-<pid>`), so it cannot collide with anyone;
- starts its **own** test mint on a private port (`--mint-port`, default `:3438`)
  and its own counting proxy (`--proxy-port`, default `:3339`), and starts its own
  artifact server (`--http-port`, default `:18998`). `:3338` is deliberately not
  the default: a resident mint may already own it.

Only processes the lane started are ever stopped. An adopted mint (`--mint-url`)
is left alone.

---

## Why assertion F is measured by a proxy, not by a log

The goal's central claim is *"between open and settle the payer makes ZERO calls
to the mint"*. That is a claim about **bytes on the wire**, so it is measured:
`spilman/mint_proxy.py` sits in front of the test mint and counts every request it
forwards. The lane arms the counter **after** open (open legitimately spends one
funding swap) and disarms it at settle. If the count is `> 0`, F **FAILS** and
prints the offending `METHOD /path` records.

A log scrape could never fail — a call the payer forgot to log is invisible — so it
is not used.

**The subtlety that makes or breaks the measurement:** a Spilman client learns mint
endpoints from the mint's own `/v1/info` and keyset responses. If the mint
advertises its own address, the payer's later calls **bypass the counter** and
"zero" becomes unfalsifiable rather than measured. So the lane starts the mint
with its **advertised `--base-url` pointing at the proxy**, and then *proves* it by
fetching the mint's advertised URL through the counter. If the lane is given an
external `--mint-url` (whose base-url it cannot retarget), F reports **SKIP with
that reason** — never a fake green.

The proxy has its own self-test (`python3 spilman/mint_proxy.py --selftest`) that
proves counting, forwarding, and arm/disarm gating against a real upstream.

---

## How to read a SKIP vs a FAIL

| You see | Means | What to do |
|---|---|---|
| `PASS` | the assertion ran and held | nothing |
| `FAIL` | the prerequisite existed and the behaviour was wrong — **including a non-zero mint-call count** | investigate; this is the real signal |
| `SKIP ... reason: ...` | a prerequisite was **absent or unmeasurable**, detected by probing | the reason names it; the lane never assumes |

The lane distinguishes three skip causes explicitly:

- **`POST /spilman/channel` is not mounted** — probed with a real POST; a `404`
  or `405` means the route does not exist on the deployed image.
- **the `spilman-payer` driver binary is not present** — probed by its own
  `sha256` verification during deploy.
- **assertion F is unmeasurable here** — the mint in use advertises its own
  address, so client-learned URLs bypass the counting proxy.

A skip is reported as a skip with its reason. It is never silently reported as a
pass. A lane that reported "pass" here would be lying about the goal's claim.

---

## The driver marker contract (the interface E–H are written against)

E–H are driven by the `spilman-payer` binary running **on the router**. It runs the
full flow and exits `0` on success, and it prints these markers to stdout (the lane
polls them over serial):

```
PAYER_OPEN_OK channel_id=<hex>
PAYER_SETTLE_OK channel_id=<hex> balance=<int> payee_credited=<int>
PAYER_EXIT=<code>
```

The lane reads them with `<<M<nonce>:value>>` sentinels. A driver that exists but
does not emit these markers is a **FAIL** (the binary does not meet the interface),
not a SKIP.

- **E** passes when `PAYER_OPEN_OK` carries a `channel_id`.
- **F** passes when the counter saw **0** requests between open and settle.
- **G** passes when `balance == payee_credited`, and the module's
  `/spilman/status` still knows the channel.
- **H** kills the module after at least one recorded payment, restarts it on the
  same private port and state dir, and requires the channel's decision to be
  **identical** before and after — then requires a **stale (lower) balance to be
  refused**, which proves the latest signature survived *and is still verified*.

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
4. **Children must not inherit the lock fds** (`8>&- 9>&-`; `close_fds=True` for
   the Python children).
5. **Identify the board by its PROMPT, never by the port.** The FTDI↔tty mapping
   drifts; a wrong port also *interrupts* whoever is on the right one.
6. **Kill only our own pid.** BusyBox has **no `pkill`**, so the lane kills the pid
   recorded in its own pidfile, falling back to `ps w | grep -F <run-dir>`. It never
   matches by process *name*: a same-named decoy must survive. `test_lane_guard.py`
   case 4 proves that.
7. **A wall-clock alarm** so a wedged shell cannot pin the bench forever.

The guards are themselves tested — a guard nobody tested is a rumour:

```
$ python3 spilman/test_lane_guard.py
case 1 (serial flock held): rc=3 PASS
case 2 (tty held, no lock): SKIP — needs a real /dev/ttyUSB* holder
case 3 (free): rc=0 PASS — guard did not block
case 4 (kill only our pid): PASS — killed=… ours_alive=False decoy_alive=True
RESULT: all guard cases behaved correctly
```

---

## Parsing the console (why there is a sentinel layer)

Two parsing bugs in the first protocol run made **green code report red**, and both
are worth knowing before touching `raw_cmd`:

1. `last_line()` returned the trailing shell **prompt**, so a `sha256` was compared
   against `root@tollgate-0195:/tmp/x#`.
2. `if ':2123 ' in out and 'NOLISTEN' not in out` was **always false**: the console
   *echoes the command*, so the grep pattern and the `|| echo NOLISTEN` fallback both
   appear in the transcript even on success.

The durable fix is not to parse free-form output at all. Commands print a
`<<M<nonce>:$V>>` sentinel built from a shell variable; the lane extracts it with
`extract_marked()`. The echoed command contains the literal `$V`, so sentinel values
containing `$` are rejected, and a per-call random nonce stops a stale sentinel from
answering the wrong question. `spilman/test_lane_parse.py` pins all of this against
**real captured transcripts**, including the echoed-command trap.

---

## Usage

```bash
# full run: deploys, exercises A–D, and runs E–H when the driver is present
python3 spilman/spilman_router_lane.py --port /dev/ttyUSB0

# --dry-run takes the locks, confirms board identity, and stops before mutating
python3 spilman/spilman_router_lane.py --port /dev/ttyUSB0 --dry-run
```

Useful flags: `--lane-port` (private module port, default `:2123`),
`--mint-port` (default `:3438`), `--proxy-port` (default `:3339`),
`--http-port` (artifact server, default `:18998`), `--module-binary`,
`--payer-binary`, `--mint-url` (adopt an existing mint instead of starting one).

---

## Evidence from the first real protocol run

Router: `tollgate-0195`, OpenWrt 24.10.2, aarch64 musl, serial `/dev/ttyUSB0`
@ 115200, identified by prompt.

```
### board identified by prompt: tollgate-0195 on /dev/ttyUSB0
### lane port 2123 (private), run dir /tmp/spilman-lane-3474453
### deployed to /tmp/spilman-lane-3474453 on the router
### counting proxy http://192.168.2.18:3339 -> http://192.168.2.18:3438
### test mint live on :3438, advertising http://192.168.2.18:3339
### proven: the mint's advertised URL answers via the proxy
### our module listening on :2123 = True

=== results ===
  PASS  [B] deploy tollgate-module-basic-rust: router sha256 == host — router=a6962677bf379803… host=a6962677bf379803…
  PASS  [A] module binary runs on the router — listening
  PASS  [A] GET /whoami answers 200 — got '200'
  PASS  [B] /spilman/params reachable (rail enabled) — HTTP '200'
  PASS  [B] params points the payer at the counting proxy — mint_url is the proxy
  PASS  [C] forged payment REFUSED — HTTP '409'
  PASS  [D] /spilman/* 404s when the rail is disabled — HTTP '404'

=== skipped, with named reasons (never silent passes) ===
  SKIP  [E] channel opens against the test mint
          reason: protocol E2E blocked: POST /spilman/channel is not mounted (HTTP '404'); the spilman-payer driver binary is not present on the deployed image
  ... F, G, H: same reason

=== 7/7 router assertions passed, 0 failed, 4 skipped ===
```

The router's own checksum **matches the host's**, so the bytes that ran are the
bytes built. `ldd` resolves every NEEDED entry on the device.

**Not touched:** no service stopped, no config written, no existing binary
replaced. The incumbent `/usr/bin/tollgate-wrt` (another context window's build,
listening on `:2121`) and `/etc/tollgate/mint-rt` were left exactly as found. Only
`/tmp/spilman-lane-<pid>` artifacts, plus a transient `/usr/lib` `.so` removed in
the same run.
