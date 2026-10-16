# TollGate Behaviour — User Stories and the Divergence Ledger

This is PRTA's behavioural contract for the TollGate family. Every
implementation is tested against the same stories; where an implementation
deliberately differs, the difference lives here as a documented tradeoff —
**diverging is allowed, silent drift is not** (US-0).

Backends (the differential axis, extending `matrix.yaml`):

| key | implementation | surface PRTA drives |
|---|---|---|
| `go` | OpenTollGate/tollgate-module-basic-go (v1) | LuCI, captive portal API, CLI socket, sessions.json |
| `rust` | Amperstrand/tollgate-rs-ai-research-and-experiments (v1-compat line) | HTTP "V1Server" API on the fork |
| `rust-basic` | Amperstrand/tollgate-module-basic-rust (Go-shaped port) | same HTTP API shape as `go` |
| `rust-v2` | OpenTollGate/tollgate-rs (current, rc.x) | native protocol: tollgated/mintd/merchantd + control socket |

Status vocabulary (in `stories.yaml`, the machine-readable twin of this file):
`pass` (a named test proves it) · `fail` (a named test disproves it) ·
`diverges` (by design; the tradeoff is written here) · `n/a` (outside this
backend's scope) · `pending` (no test yet) · `pending-venue` (needs QEMU /
physical bench) · `pending-audit` (backend exists, story not yet mapped).

---

## US-0 — Divergence is documented, not accidental

> As an ecosystem maintainer, I want every behavioural difference between
> implementations recorded with its design rationale, so that divergence is
> a decision I can find — not drift I have to archaeology out of a diff.

Acceptance: every `diverges` status in `stories.yaml` names its tradeoff
here; a new implementation enters the table with a full story map, not an
empty one.

---

## Group P — What a purchase is

### US-P1 — Prepaid, never on credit
> As a seller, I want nothing delivered before it is paid for, so that an
> unknown peer can never cost me money.

Acceptance: with no payment, no service beyond the first-contact allowance
(US-E1); delivery begins only after value lands.
Divergences: none — this is the family invariant. *v1* enforces it at the
portal (no MAC auth until the token clears); *v2* at the enforcer (no
channel, no class).

### US-P2 — The metered unit is what the operator delivers
> As a buyer, I want to pay in the unit I consume, so that the price means
> what it says.

Acceptance: the unit is declared and visible in the node's own state.
**Diverges by design**: `go`/`rust-basic` sell **minutes of access**
(`price_per_minute`) — the simplest thing a portal can meter. `rust-v2`
sells **bytes** (one voucher = one byte), enabling rate as a product. The
Amperstrand experimental line extends this to **watt-hours** (charger
hosts) — the resource-agnostic core's whole point. Tradeoff: time is
human-legible and cheap to meter; bytes/Wh need a kernel enforcer but make
*speed* sellable.

### US-P3 — Reserving a rate buys time at a speed
> As a buyer with standing demand, I want the node to reserve headroom over
> what I use, so that my traffic is never throttled below what I asked for.

Acceptance: observed/declared demand × headroom becomes the reserved rate;
the enforcer shapes to it. (v2: default headroom 125%.)

### US-P4 — Without a reservation, pay per use
> As an occasional buyer, I want to reserve nothing and pay only for what I
> moved, so that idle time is free.

Acceptance: a peer with zero reserved rate is drawn only for usage.

### US-P5 — The budget has a deadline: renewed, and what lapses is forfeit
> As a seller, I want prepaid budget to expire, so that a stale reservation
> cannot hold capacity forever; and as a buyer, I want it renewed before it
> lapses while I still want service.

Acceptance: budget expiry is finite and extends on purchase; an unrenewed
budget lapses and the remainder is not owed back (v2: forfeit by design —
the seller already reserved the capacity).

### US-P6 — A purchase takes effect on arrival
> As a buyer behind a flaky link, I want each payment to stand alone, so
> that a lost acknowledgement costs me nothing.

Acceptance (v2): channel state is cumulative and signed; a duplicate or
lost message cannot corrupt it. (v1 N/A: tokens are one-shot and
idempotent-by-uniqueness — a *different* answer to the same story; see
`matrix.yaml` invariants I3/I6 for the fault-injected versions.)

---

## Group E — Admission and enforcement

### US-E1 — The stranger is carried far enough to pay
> As a seller, I want an unknown peer held at a minimum flow rather than
> blocked, so that it can reach my mint and become a customer with no
> out-of-band setup.

**Diverges by design**: `rust-v2` carries the stranger at a small minimum
flow (no accounts, no registration — the protocol's bootstrapping story).
`go`/`rust-basic` **block at the portal** until payment: simpler to reason
about and audit, at the cost of first contact needing the captive portal
UX. Tradeoff: zero-setup peering vs. a human-facing web flow.

### US-E2 — The enforcer holds a peer to what it bought
> As a seller, I want enforcement measured in the delivered unit, so that
> the promise and the delivery are the same number.

Acceptance: throughput is asserted in bytes moved (not elapsed time)
against the bought rate. (v1 equivalent: session minutes → portal
deauth at expiry.)

### US-E3 — When the money runs out, the gate closes
> As a seller, I want service to end when payment ends, enforced by the
> box itself, so that I do not have to trust the client.

Acceptance: budget/session exhausted ⇒ traffic stops (v2: enforcer gate;
v1: expiry kick, `test_expiry_kick.py`).

### US-E4 — The operator caps what it will promise
> As an operator on a constrained uplink, I want a total reservation cap
> across all payers, so that no sum of buyers can promise more than I can
> carry.

Acceptance (v2): a TopUp that would exceed `grants.max_rate` is refused
with the rate still free attached; the buyer is shaped at what remains —
refused, not disconnected.

---

## Group R — Lifetime and resilience

### US-R1 — Budgets survive a restart
> As a buyer, I want my prepaid budget kept on disk, so that a seller
> reboot does not eat what I paid for.

Acceptance: after gateway restart the budget is handed back on reconnect.

### US-R2 — A blipped link resumes its channels
> As a buyer, I want a dropped connection to resume on redial, so that a
> Wi-Fi blip does not re-fund and re-trust from zero.

Acceptance (v2): same channel ids after reconnect; no new funding on
either side.

### US-R3 — Channels roll over, buying continues
> As a buyer moving serious traffic, I want payment capacity to roll over
> seamlessly when a channel fills, so that throughput never collapses at a
> boundary.

Acceptance (v2): with channels sized to exhaust quickly, several rollovers
occur and the bought rate is maintained throughout.

### US-R4 — A failed settlement is retried, not lost
> As a seller, I want settlement retries, so that a mint blink does not
> strand the channel (and the money in it).

---

## Group M — Money and audit

### US-M1 — No funds disappear (invariant I1)
> As an auditor, I want mint ledger Δ == wallet Δ + paid out, so that
> value is conserved through every failure mode.

### US-M2 — One payment, one session (invariant I2)
> As an auditor, I want retries, replays and restarts to never mint a
> second session from one payment.

### US-M3 — The operator can extract what it earned
> As an operator, I want my revenue spendable, so that the gateway is a
> business, not a vault.

**Diverges by design**: `go` sweeps to **Lightning addresses with
profit-share splits** (payout scheduling, min-payout amounts). `rust-v2`
settles into **its own mint / the market** (sat-mint with a real Lightning
backend when the market lands). Tradeoff: v1's model pays out to the
existing Lightning economy today; v2's model keeps hop-by-hop liquidity
on-ledger (the operator's margin is the spread) and defers the fiat/Lightning
edge to the market layer.

### US-M4 — Reused value buys nothing
> As a seller, I want double-spent or replayed tokens/updates rejected, so
> that old paper is worthless.

---

## Group I — Identity, pricing and scope

### US-I1 — A peer is its key
> As a seller, I want peers identified by the keys they sign with (v2:
> bound to the connection address), so that identity needs no registry.

### US-I2 — The operator prices their own resource
> As an operator, I want to set my price and choose which mints I trust,
> so that my margin is mine. (v1: `price_per_minute` + `accepted_mints`;
> v2: per-mint prices on the market, `vouchers.accepted_mints`.)

### US-I3 — The unit is declared, not assumed
> As an embedder, I want the metered unit declared per node (`byte`,
> `sat`, `watt-hour`), so that the same core sells different resources.

---

## Maintaining the map

1. A behaviour change in any implementation updates `stories.yaml` (and
   this file if the story or a tradeoff changed) in the same PR.
2. A new implementation gets a backend row and a full story map before
   its first PRTA run — `pending-audit` is the honest starting state,
   not an empty file.
3. `scripts/run-behavior.sh` joins live test results with `stories.yaml`
   and writes the differential report; parity is measured, not asserted.
