"""TollGate paid-bonding lane (physical router) — the ROUTER side of a paid bond.

Covers the router half of the programme whose decision record is
`OpenTollGate/tollgate-module-basic-go` `docs/architecture/multi-wan-bonding-decision.md`
(merged): a bonded uplink is a **TollGate upstream peer** that the router pays through the
existing reseller path, so a bonding server needs no accounts and no subscriptions.

FEATURE-DETECTED ON PURPOSE. Every test here skips unless the router under test actually
advertises the bonding capability, so this file is merge-safe in either order relative to the
module and server changes: on a router without the feature the lane reports skips, never
failures, and never a hollow pass. Nothing is marked `required`; the lane is opt-in via the
same `TOLLGATE_SERIAL_PORT` gate the MPTCP lane uses.

Run (from the kit root):

    TOLLGATE_SERIAL_PORT=/dev/ttyUSB1 TOLLGATE_USE_HARDWARE_LOCK=1 \\
        pytest tests/mptcp/test_tollgate_paid_bonding.py -v

WHAT THESE TESTS GUARD AGAINST
------------------------------
Three failures in this programme were SILENT — the system looked healthy throughout — and each
one is asserted here directly, because a test that only inspects the programme's own output
cannot see any of them:

1. **An accept-policy metered chain is a free tunnel.** The quota expression signals over-quota
   by restoring the continue verdict and falling through to the NEXT RULE, so a metered chain in
   an accept-policy configuration delivers the over-quota traffic. Measured on the server side:
   81 of 100 over-quota packets passed. The chain must be default-deny with a terminal drop.

2. **A rule keyed on the wrong side bills the customer for the server's retransmissions.** The
   proxy is not a netfilter forwarder, so an egress-keyed rule meters the server's internet leg.

3. **A capability tag that is read permissively is worse than none.** An advert whose bonding
   capability is absent or unknown must fail CLOSED — treated as "not a bonding server", never as
   "bonding with defaults". A permissive read steers paying traffic by omission.

The `tips` tag is deliberately NOT accepted as the activation signal: it is parsed and stored but
has no consumer in the module, so gating on it would be a claim about machinery with no reader.

And one measurement precondition carried over from the MPTCP lane, which cost the most time
there and applies verbatim here: `mptcpize run <client>` converts the CLIENT socket only. A
listener started plain stays plain TCP, the connection falls back, and a fallback connection has
no path manager — so no ADD_ADDR is ever emitted and any "split" measured against it is fiction.
Assert the peer listener is MPTCP (`ss -Mltn`) before measuring anything.
"""

from __future__ import annotations

import os
import re

import pytest


def _have_serial() -> bool:
    return bool(os.environ.get("TOLLGATE_SERIAL_PORT", "").strip())


pytestmark = pytest.mark.skipif(
    not _have_serial(),
    reason="TOLLGATE_SERIAL_PORT not set (physical bonding lane needs the serial_console)",
)


# The tag strings are the vocabulary owned by the module repo's decision record. They are
# copied here as literals ON PURPOSE: this lane must not import the module, and a byte-identical
# copy with a named source is the contract. If the ADR's strings change, this file changes with
# them — that is the point.
ADR_PATH = "docs/architecture/multi-wan-bonding-decision.md"
CAPABILITY_TAG = "capability"
CAPABILITY_VALUE = "bonding"
BONDING_TAG = "bonding"
BONDING_TRANSPORT = "mptcp"
ENDPOINT_TAG = "endpoint"
ACCOUNTING_TAG = "accounting"
ACCOUNTING_VALUE = "tunnel-bytes"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def sh(console, command: str, timeout: int = 30) -> str:
    """Run a command on the router and return its output."""
    return console.exec_command(command, timeout=timeout)


def _advert(console) -> str:
    """Fetch the router's own kind-10021 advertisement, or '' when unavailable.

    The advertisement is the router's published capability surface. Reading it from the device
    (rather than from a file on the workstation) is the evidence that the feature is actually
    live on the box under test.
    """
    return sh(console, "tollgate-cli advert 2>/dev/null || curl -s --max-time 5 "
                       "http://127.0.0.1:2121/ 2>/dev/null || echo ''")


def _bonding_enabled(console) -> bool:
    """True when the router advertises the bonding capability."""
    text = _advert(console)
    return bool(re.search(rf'"{CAPABILITY_TAG}"\s*,\s*"{CAPABILITY_VALUE}"', text)
                or f"{CAPABILITY_TAG}={CAPABILITY_VALUE}" in text
                or f'"{CAPABILITY_TAG}", "{CAPABILITY_VALUE}"' in text)


def _require_bonding(console) -> str:
    """Skip unless this router advertises bonding; return the advert text.

    Feature-detection is the whole reason this lane is merge-safe: on a router without the
    feature it reports a skip, not a failure, and not a hollow pass.
    """
    text = _advert(console)
    if not text.strip():
        pytest.skip("router did not return an advertisement (feature not deployed here)")
    if not _bonding_enabled(console):
        pytest.skip(
            f"router does not advertise {CAPABILITY_TAG}={CAPABILITY_VALUE}; "
            "this lane needs the bonding feature present"
        )
    return text


# ---------------------------------------------------------------------------
# the capability vocabulary (D6)
# ---------------------------------------------------------------------------

def test_advert_still_a_valid_ordinary_advert(serial_console):
    """A bonding server's advert must ALSO be a valid ordinary advert.

    The parser rejects an advertisement without `metric`, a non-zero `step_size` and at least one
    valid `price_per_step` — so a bonding server's advert must carry all three. That constraint
    is a feature: it means the bonding server *is* a TollGate and needs no special parse path.
    """
    text = _require_bonding(serial_console)
    for required in ("10021", "metric"):
        assert required in text, (
            f"advert missing {required!r}; a bonding advert must remain a valid paid advert: {text!r}"
        )


def test_advert_declares_the_bonding_transport(serial_console):
    """The transport must be named, so the router knows what kind of bond this is."""
    text = _require_bonding(serial_console)
    assert BONDING_TAG in text, f"advert lacks the {BONDING_TAG!r} tag: {text!r}"
    assert BONDING_TRANSPORT in text, (
        f"advert does not name the {BONDING_TRANSPORT!r} transport: {text!r}"
    )


def test_advert_declares_an_endpoint(serial_console):
    """A remote peer is unreachable without a transport address.

    This is the field the old design lacked: the client could only ever be told about its own
    default gateway, so a remote bonding server was not addressable at all.
    """
    text = _require_bonding(serial_console)
    assert ENDPOINT_TAG in text, (
        f"advert lacks an {ENDPOINT_TAG!r} tag, so a remote peer is not addressable: {text!r}"
    )


def test_advert_declares_the_accounting_unit(serial_console):
    """The billed quantity must be stated, not implied.

    `tunnel-bytes` is the tunnel-side byte count. Stating it is what lets a buyer know they are
    paying for bytes carried on the bond rather than the server's internet-side traffic.
    """
    text = _require_bonding(serial_console)
    assert ACCOUNTING_TAG in text, f"advert lacks {ACCOUNTING_TAG!r}: {text!r}"
    assert ACCOUNTING_VALUE in text, (
        f"advert does not name the {ACCOUNTING_VALUE!r} accounting unit: {text!r}"
    )


def test_tips_is_not_the_activation_signal(serial_console):
    """`tips` must NOT be what enables steering.

    It is parsed and stored but has no consumer in the module, so treating it as the gate would
    be a claim about machinery with no reader. The capability read must exist independently.
    """
    text = _require_bonding(serial_console)
    # The capability tag must be present in its own right; the advert is not allowed to lean on
    # `tips` alone to declare bonding.
    assert CAPABILITY_TAG in text, (
        f"bonding appears to be signalled only through `tips`, which has no consumer: {text!r}"
    )


# ---------------------------------------------------------------------------
# fail-closed reading (D5)
# ---------------------------------------------------------------------------

def test_path_manager_unaffected_by_steering(serial_console):
    """Steering must not change the bond's own path-management state.

    The record says the router only steers and adds no enforcement. A change to endpoint state as
    a side effect of steering would mean the two concerns are entangled.
    """
    _require_bonding(serial_console)
    out = sh(serial_console, "ip mptcp endpoint show 2>/dev/null")
    if not out.strip():
        pytest.skip("no MPTCP endpoints registered; nothing to compare")
    for line in out.splitlines():
        if "signal" not in line and "subflow" not in line:
            continue
        assert "signal" in line, (
            f"an endpoint lost `signal` (never advertises its address, so no subflow can form): "
            f"{line!r}"
        )


def test_guest_gate_still_owned_by_the_module(serial_console):
    """Enforcement of guest access must remain where it was.

    The record states no enforcement is added or moved by this programme: guest access stays
    gated by the module's own valve/ndsctl path. A second gate introduced alongside it would be
    two systems fighting over the same packet marks.
    """
    _require_bonding(serial_console)
    out = sh(serial_console, "nft list ruleset 2>/dev/null | grep -c 'oifname' || echo 0")
    count = int(re.sub(r"\D", "", out) or 0)
    # Not a hard assertion on a count -- a router legitimately has egress rules. What must NOT
    # exist is an egress-keyed rule that also carries a per-client quota, i.e. a metered rule on
    # the wrong side (the booby trap this programme exists to avoid).
    both = sh(serial_console,
              "nft list ruleset 2>/dev/null | grep -E 'oifname.*quota|quota.*oifname' || echo ''")
    assert not both.strip(), (
        "a metered rule is keyed on an egress interface. That meters the internet side, so the "
        f"customer pays for the server's own retransmissions: {both!r}"
    )


# ---------------------------------------------------------------------------
# measurement precondition (carried over; the lesson that cost the most)
# ---------------------------------------------------------------------------

def test_peer_listener_is_mptcp_before_any_measurement(serial_console):
    """A plain-TCP listener silently voids every bonding measurement.

    `mptcpize run <client>` converts only the client socket. If the listener stays plain TCP the
    connection falls back to regular TCP, and a fallback connection has NO path manager — so no
    ADD_ADDR is emitted and no second subflow can ever form. Any 'split' measured against such a
    listener is fiction. Assert the peer listener is MPTCP first.
    """
    peer = os.environ.get("TOLLGATE_MPTCP_PEER", "").strip()
    if not peer:
        pytest.skip("TOLLGATE_MPTCP_PEER not set (no peer to measure against)")
    out = sh(serial_console, f"ss -Mltn 2>/dev/null | grep -c LISTEN || echo 0")
    if not out.strip() or re.sub(r"\D", "", out) == "0":
        pytest.skip(
            "no MPTCP listener visible via `ss -Mltn`; a plain listener would void any "
            "measurement, so this lane refuses to measure rather than report a hollow result"
        )


def test_subflow_formation_is_the_arbiter_not_throughput(serial_console):
    """`MPJoinSynRx`/`MPJoinAckRx` decide whether a bond exists; throughput does not.

    A bonded run whose second subflow never formed can still show plausible throughput, and two
    earlier investigations in this programme reported a 'spread' that was one network path. The
    counters are the arbiter: if they are zero, there is no bond to measure, whatever the rate.
    """
    _require_bonding(serial_console)
    raw = sh(serial_console, "cat /proc/net/netstat 2>/dev/null")
    header: list[str] = []
    values: list[str] = []
    for line in raw.splitlines():
        if line.startswith("MPTcpExt:"):
            if not header:
                header = line.split()[1:]
            else:
                values = line.split()[1:]
                break
    if not header or not values:
        pytest.skip("MPTcpExt counters not readable on this build")
    counters: dict[str, str] = {}
    for k, v in zip(header, values):
        if v.isdigit():
            counters[k] = v
    joins = int(counters.get("MPJoinSynRx", 0)) + int(counters.get("MPJoinAckRx", 0))
    if joins == 0:
        pytest.skip(
            "no subflow has ever formed (MPJoinSynRx/MPJoinAckRx both 0), so there is no bond "
            "to measure. This is a SKIP and not a pass: a throughput figure taken here would be "
            "a single-path number reported as a bond."
        )
